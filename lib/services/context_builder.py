"""
Context builder service - constructs conversation context.
Single responsibility: Building message context with personality and memories.
"""
import json
import logging
from collections import deque

from lib.services.memory_service import MemoryService


class MessageContext:
    """Container for message context components."""

    def __init__(self):
        self.system_messages = []
        self.original_messages = []

    def add_system_message(self, content: str):
        """Add a system message to context."""
        self.system_messages.append({"role": "system", "content": content})

    def add_message(self, message: dict):
        """Add a pre-built message dict (any role) to context."""
        self.system_messages.append(message)

    def add_original_messages(self, messages: list[dict]):
        """Add original messages."""
        self.original_messages = messages

    def build(self) -> list[dict]:
        """Build final message list."""
        return self.system_messages + self.original_messages


class ContextBuilder:
    """Builds conversation context with personality and memories."""

    def __init__(
        self,
        system_prompt_provider,
        memory_service: MemoryService | None = None,
        memory_window_turns: int = 3,
    ):
        """
        Initialize context builder.

        Args:
            system_prompt_provider: Provider for system prompts
            memory_service: Optional memory service
            memory_window_turns: Number of past turns whose recalled memories are
                carried forward into the current context (sliding window).  Set to
                0 to disable.  Default: 3.
        """
        self.system_prompt_provider = system_prompt_provider
        self.memory_service = memory_service
        self._memory_window_turns = memory_window_turns
        # Per-conversation deque of raw memory-dict lists (one list per turn).
        # Capped at memory_window_turns entries — oldest drops off automatically.
        self._memory_windows: dict[str, deque[list[dict]]] = {}

    async def build_context(
        self,
        messages: list[dict],
        user_id: str | None = None,
        conversation_id: str | None = None,
        enable_personality: bool = True,
        enable_memory: bool = True,
        display_name: str | None = None,
        channel_name: str | None = None,
        guild_name: str | None = None,
        is_dm: bool = False,
        user_name: str | None = None,
    ) -> list[dict]:
        """
        Build conversation context.

        Args:
            messages: Original messages
            user_id: User identifier
            conversation_id: Conversation identifier for scoping
            enable_personality: Include personality prompt
            enable_memory: Include memories
            display_name: Human-readable display name
            channel_name: Channel name
            guild_name: Server / group name
            is_dm: Whether this is a direct message
            user_name: Raw username (e.g. '<username>')

        Returns:
            Enhanced message list
        """
        context = MessageContext()

        # Add personality prompt
        if enable_personality:
            await self._add_personality(context)

        # Add user_info tool message (must be second slot, right after system prompt)
        if enable_personality and user_id:
            self._add_user_context(
                context, user_id, conversation_id,
                display_name=display_name,
                channel_name=channel_name,
                guild_name=guild_name,
                is_dm=is_dm,
                user_name=user_name,
            )

        # Add memories (scoped by user_id and conversation_id)
        if enable_memory and user_id and self.memory_service:
            await self._add_memories(context, messages, user_id, conversation_id)

        # Surface any sandbox jobs that completed since the last turn
        self._add_completed_jobs(context)

        # Surface any background tasks (research, etc.) completed since the last turn
        self._add_task_notifications(context)

        # Add original messages
        context.add_original_messages(messages)

        return context.build()

    async def _add_personality(self, context: MessageContext):
        """Add personality prompt to context."""
        try:
            prompt = await self.system_prompt_provider.get_prompt()
            context.add_system_message(prompt)
        except Exception as e:
            logging.error(f"Error loading personality prompt: {e}")

    def _add_user_context(
        self,
        context: MessageContext,
        user_id: str | None,
        conversation_id: str | None = None,
        *,
        display_name: str | None = None,
        channel_name: str | None = None,
        guild_name: str | None = None,
        is_dm: bool = False,
        user_name: str | None = None,
    ):
        """
        Inject a ``role=tool, name=user_info`` message immediately after the
        system prompt so Nami always knows who she is talking to.

        The message carries platform, user, channel, and guild metadata as a
        JSON payload — safe defaults (None / false) for missing fields.
        """
        platform: str | None = None
        scoped_user_id = user_id
        raw_username = user_name

        if user_id and ":" in user_id:
            prefix, raw_id = user_id.split(":", 1)
            platform = prefix.capitalize()
            if raw_username is None:
                raw_username = raw_id
        elif raw_username is None:
            raw_username = user_id

        user_info = {
            "user": display_name,            # "Zero"
            "username": raw_username,        # "<username>"
            "user_id": scoped_user_id,       # "discord:123456789"
            "platform": platform,            # "Discord"
            "channel": channel_name,         # "#lab-chat"
            "guild": guild_name,             # "Zero Lab"
            "is_dm": is_dm,                  # false
        }

        context.add_message({
            "role": "tool",
            "name": "user_info",
            "content": json.dumps(user_info),
        })

    async def _add_memories(
        self,
        context: MessageContext,
        messages: list[dict],
        user_id: str,
        conversation_id: str | None,
    ):
        """
        Add relevant memories to context using a sliding window.

        For the current turn, memories are retrieved by searching with the last
        user message.  Memories recalled in the previous ``memory_window_turns``
        turns are merged in (deduplicated by memory_id / text) so that context
        established a few messages ago is not silently lost when the topic
        drifts only slightly.

        Only the *current turn's* fresh fetch is pushed into the window — the
        merged set is never stored — so the window cannot snowball over time.
        """
        try:
            user_messages = [msg for msg in messages if msg.get("role") == "user"]
            if not user_messages:
                return

            last_user_msg = user_messages[-1].get("content", "")
            if not last_user_msg:
                return

            # Resolve cross-platform identities so memories are shared
            all_user_ids = [user_id]
            try:
                resolved = await self.memory_service.memory_db.resolve_canonical_users(user_id)
                if resolved:
                    all_user_ids = resolved
            except Exception:
                pass  # Graceful fallback if identity resolution fails

            # --- Step 1: fetch this turn's memories (raw dicts) ------------------
            current_turn: list[dict] = []
            seen_ids: set[str] = set()

            for uid in all_user_ids:
                memories = await self.memory_service.retrieve_relevant_memories(
                    query=last_user_msg,
                    user_id=uid,
                    top_k=5,
                    context_k=20,
                )
                for mem in memories:
                    mid = mem.get("memory_id") or mem.get("text", "").strip()
                    if mid and mid not in seen_ids:
                        seen_ids.add(mid)
                        current_turn.append(mem)

            # --- Step 2: merge memories from the sliding window ------------------
            all_memories: list[dict] = list(current_turn)
            if conversation_id and self._memory_window_turns > 0:
                window = self._memory_windows.get(conversation_id, deque())
                for prev_turn in window:
                    for mem in prev_turn:
                        mid = mem.get("memory_id") or mem.get("text", "").strip()
                        if mid and mid not in seen_ids:
                            seen_ids.add(mid)
                            all_memories.append(mem)

            # --- Step 3: format and inject ---------------------------------------
            formatted = self.memory_service.format_memories(all_memories)
            if formatted:
                context.add_system_message(formatted)

            # --- Step 4: push this turn into the window (current fetch only) -----
            if conversation_id and self._memory_window_turns > 0 and current_turn:
                if conversation_id not in self._memory_windows:
                    self._memory_windows[conversation_id] = deque(maxlen=self._memory_window_turns)
                self._memory_windows[conversation_id].append(current_turn)

        except Exception as e:
            logging.error(f"Error adding memories: {e}", exc_info=True)

    def _add_task_notifications(self, context: MessageContext) -> None:
        """
        Inject background task completions (research, etc.) into context so Nami
        knows what she's been up to between conversations.

        Drains the ``TaskNotificationQueue`` — notifications are shown once, the
        next time Nami is spoken to after the task finished.
        """
        try:
            from lib.global_registry import g_data
            queue = g_data.get("task_notification_queue")
            if not queue:
                return
            notifications = queue.pop_pending()
            if not notifications:
                return
            lines = ["[Background Tasks] Completed since your last conversation:"]
            for n in notifications:
                title = n.get("title", "Unknown task")
                task_type = n.get("task_type", "task")
                # Research/dream use "summary"; scheduled tasks use "result"
                summary = (n.get("summary") or n.get("result") or "").strip()
                entry = f"  • [{task_type}] {title}"
                if summary:
                    entry += f" — {summary}"
                lines.append(entry)
            context.add_system_message("\n".join(lines))
        except Exception as e:
            logging.error(f"Error adding task notifications to context: {e}", exc_info=True)

    def _add_completed_jobs(self, context: MessageContext) -> None:
        """Inject any sandbox jobs that completed since the last conversation turn."""
        try:
            from lib.global_registry import g_data
            sandbox = g_data.get("sandbox_manager")
            if not sandbox:
                return
            completed = sandbox.pop_unnotified_completed()
            if not completed:
                return
            lines = ["[Sandbox] Background job(s) completed since your last message:"]
            for job in completed:
                preview = (job.get_output()[:500] or "(no output)").strip()
                lines.append(
                    f"  job_id={job.job_id} | exit_code={job.exit_code} "
                    f"| elapsed={round(job.elapsed_seconds(), 1)}s"
                )
                lines.append(f"  command: {job.command}")
                lines.append(f"  output:\n{preview}")
            context.add_system_message("\n".join(lines))
        except Exception as e:
            logging.error(f"Error adding completed jobs to context: {e}", exc_info=True)
