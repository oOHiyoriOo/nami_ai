"""
dream.py — HeartbeatModule: Auto-Dream memory consolidation.

Migrates DreamService's poll loop into a HeartbeatModule. The idle gate
(min_idle_hours), new memory threshold, and dream execution are all handled
within this module while HeartbeatService owns the tick.

DreamService.record_activity() is still called by the chat pipeline to
reset the idle timer.
"""

import asyncio
import logging
import time

from lib.global_registry import g_data
from lib.services.heartbeat_module import HeartbeatModule
from lib.utils.ai_lock import acquire_ai_lock


class DreamModule(HeartbeatModule):
    """
    Auto-Dream: Nami reflects on her memory graph during downtime.

    Gates (checked in condition()):
    1. Dream enabled in config?
    2. Idle time >= min_idle_hours?
    3. New memories >= min_new_memories since last dream?
    4. No dream already running?

    action() runs the dream agent with dream_tools.
    """

    name = "dream"
    priority = 25
    cooldown_seconds = 60  # Check gates frequently; real gating is in condition()

    def __init__(
        self,
        config,
        db_path: str = "scheduler.db",
    ) -> None:
        super().__init__()
        self.config = config
        self.db_path = db_path
        mem_cfg = config.data.get("memory", {})
        self._dream_cfg = config.data.get("dream", {})
        self._min_idle_hours: float = self._dream_cfg.get("min_idle_hours", 2.0)
        self._min_new_memories: int = self._dream_cfg.get("min_new_memories", 5)
        self._max_tool_calls: int = self._dream_cfg.get("max_tool_calls", 40)
        self._dream_provider: str | None = self._dream_cfg.get("provider")
        self._dream_model: str | None = self._dream_cfg.get("model")
        self._fallback_provider: str = mem_cfg.get("extraction_provider", "ollama")
        self._fallback_model: str = mem_cfg.get("extraction_model", "llama3.2")
        # Dreams only during nighttime (hour >= night_start OR hour < night_end).
        # Defaults: 20:00–06:00 — mirrors curiosity's daytime window.
        self._night_start_hour: int = self._dream_cfg.get("night_start_hour", 20)
        self._night_end_hour: int = self._dream_cfg.get("night_end_hour", 6)
        self._active_dream = None
        self._last_message_at: float = 0.0
        self._db_initialised: bool = False

        logging.info(
            f"[dream] DreamModule initialized — idle_hours={self._min_idle_hours}, "
            f"min_new_memories={self._min_new_memories}, max_tool_calls={self._max_tool_calls}, "
            f"dream_model={self._dream_provider or self._fallback_provider}/{self._dream_model or self._fallback_model}, "
            f"nighttime={self._night_start_hour:02d}:00–{self._night_end_hour:02d}:00"
        )

    def record_activity(self) -> None:
        """Call this on every incoming message to update last_message_at."""
        self._last_message_at = time.time()

    def _is_nighttime(self) -> bool:
        """Return True if the current local hour is within the configured nighttime window."""
        import datetime
        hour = datetime.datetime.now().hour
        return hour >= self._night_start_hour or hour < self._night_end_hour

    # ------------------------------------------------------------------
    # HeartbeatModule interface
    # ------------------------------------------------------------------

    async def condition(self) -> bool:
        """
        Return True if all dream gates pass.

        Gates (checked in order):
        1. Dream enabled in config?
        1.5. Current time is within nighttime window?
        2. Idle time >= min_idle_hours?
        3. New memories >= min_new_memories since last dream?
        4. No dream already running?

        Mutual exclusion with chat and research is handled by acquiring
        g_data['ai_lock'] in action() — no explicit cross-module checks needed.
        """
        if not self._db_initialised:
            await self._init_db()
            self._db_initialised = True

        # Gate 1: dream enabled in config?
        if not self._dream_cfg.get("enabled", True):
            return False

        # Gate 1.5: only dream during nighttime window
        if not self._is_nighttime():
            import datetime
            hour = datetime.datetime.now().hour
            logging.debug(
                f"[dream] Time gate: hour={hour:02d}:xx is outside nighttime window "
                f"({self._night_start_hour:02d}:00–{self._night_end_hour:02d}:00)"
            )
            self._report_gate_block(
                "1.5",
                f"hour={hour:02d} outside nighttime "
                f"{self._night_start_hour:02d}:00–{self._night_end_hour:02d}:00",
                log_interval=7200.0,  # expected daytime block — log every 2h max
            )
            return False

        self._clear_gate_block("1.5")

        # Gate 1.8: don't dream while Nami is actively researching
        curiosity = g_data.get("curiosity_module")
        if curiosity is not None:
            active_task = getattr(curiosity, "_active_task", None)
            if active_task and not active_task.done():
                logging.debug("[dream] Research session in progress — deferring dream")
                self._report_gate_block("1.8", "curiosity _active_task still running")
                return False
        # Also check the DB in case the in-memory reference is stale
        try:
            import aiosqlite
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT count(*) FROM research_queue WHERE status = 'in_progress'"
                ) as cur:
                    row = await cur.fetchone()
            if row and row[0] > 0:
                logging.debug(
                    f"[dream] {row[0]} research topic(s) in_progress — deferring dream"
                )
                self._report_gate_block(
                    "1.8",
                    f"{row[0]} research_queue topic(s) still in_progress",
                )
                return False
        except Exception:
            pass  # research_queue may not exist yet — that's fine, don't block dream

        self._clear_gate_block("1.8")

        # Gate 2: idle long enough?
        current_activity = self._last_message_at
        await self._set_state("last_message_at", current_activity)

        idle_seconds = time.time() - current_activity if current_activity > 0 else 0
        idle_hours = idle_seconds / 3600
        if idle_hours < self._min_idle_hours:
            logging.debug(
                f"[dream] Idle gate: {idle_hours:.1f}h < {self._min_idle_hours}h required"
            )
            self._report_gate_block(
                "2",
                f"idle only {idle_hours:.2f}h, need {self._min_idle_hours}h",
            )
            return False

        self._clear_gate_block("2")

        # Gate 3: enough new memories since last dream?
        last_dream_at = await self._get_state("last_dream_at", default=0.0)
        memory_db = g_data.get("memory_db")
        if memory_db:
            new_count = await self._count_new_memories(memory_db, last_dream_at)
            if new_count < self._min_new_memories:
                logging.debug(
                    f"[dream] Memory gate: {new_count} new memories < {self._min_new_memories} required"
                )
                self._report_gate_block(
                    "3",
                    f"only {new_count} new memories, need {self._min_new_memories}",
                )
                return False
        else:
            logging.debug("[dream] memory_db not available — skipping dream")
            self._report_gate_block("3", "memory_db not available")
            return False

        self._clear_gate_block("3")

        # Gate 4: no dream already running?
        if self._active_dream and not self._active_dream.done():
            logging.debug("[dream] Dream already in progress — skipping")
            self._report_gate_block("4", "dream task already running")
            return False

        self._clear_gate_block("4")

        logging.info(
            f"[dream] All gates passed — idle={idle_hours:.1f}h, "
            f"new_memories={new_count}. Starting dream."
        )
        # NOTE: last_dream_at is updated AFTER successful completion in _run_dream(),
        # not here. This ensures a crash mid-dream doesn't silently skip those memories.
        return True

    async def action(self) -> None:
        """Schedule the dream agent behind the shared AI lock.

        Queues behind any in-progress chat message or research session naturally —
        no cross-module checks needed. The lock is the single source of truth.
        """
        async def _locked_dream() -> None:
            lock = g_data.get("ai_lock")
            if lock:
                if not await acquire_ai_lock(lock, label="dream"):
                    logging.error("[dream] Lock holder inactive — skipping dream")
                    return
                try:
                    await self._run_dream()
                finally:
                    lock.release()
            else:
                await self._run_dream()  # fallback: lock not ready yet

        self._active_dream = asyncio.create_task(_locked_dream(), name="dream_agent")

    # ------------------------------------------------------------------
    # Internal — SQLite state
    # ------------------------------------------------------------------

    async def _init_db(self) -> None:
        """Create the dream_state table if it doesn't exist."""
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS dream_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await db.commit()
        stored = await self._get_state("last_message_at", default=0.0)
        self._last_message_at = stored if stored > 0 else time.time()

    async def _get_state(self, key: str, default: float = 0.0) -> float:
        """Read a float value from dream_state table."""
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value FROM dream_state WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
        return float(row[0]) if row else default

    async def _set_state(self, key: str, value: float) -> None:
        """Write a float value to dream_state table."""
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO dream_state (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Internal — memory counting and dream execution
    # ------------------------------------------------------------------

    async def _count_new_memories(self, memory_db, since_timestamp: float) -> int:
        """Count memories created after `since_timestamp` (epoch seconds)."""
        try:
            since_ms = int(since_timestamp * 1000)
            driver = memory_db.get_driver()
            total = 0
            async with driver.session() as session:
                for label in memory_db.MEMORY_TYPES:
                    res = await session.run(
                        f"MATCH (m:{label}) WHERE m.creationTimestamp > $since RETURN count(m) AS n",
                        {"since": since_ms},
                    )
                    record = await res.single()
                    total += record["n"] if record else 0
            return total
        except Exception as e:
            logging.warning(f"[dream] Failed to count new memories: {e}")
            return 0

    async def _run_dream(self) -> None:
        """Run the dream agent — one focused AI call with heartbeat-filtered tools."""
        from lib.ai_providers import Message, ProviderRegistry
        from lib.services.tool_executor import execute_tool_loop
        from lib.services.tool_context import ToolContext

        _DREAM_SYSTEM_PROMPT = (
            "You are performing a Dream — a quiet, reflective pass over your own memory graph.\n"
            "Your purpose is to curate and improve your memories so future conversations start with cleaner, more accurate context.\n\n"
            "You have access to seven tools: dream_get_stats, dream_list_memories, dream_search_memories,\n"
            "dream_get_memory, dream_update_memory, dream_delete_memory, dream_merge_memories.\n\n"
            "Work through these phases in order:\n\n"
            "## Phase 1 — Orient\n"
            "Call dream_get_stats to see what you're working with.\n"
            "Call dream_list_memories to review the newest memories (limit=30). Get a feel for recent activity.\n\n"
            "## Phase 2 — Deduplicate\n"
            "For each new memory, call dream_search_memories with its core content.\n"
            "If you find a near-duplicate (very similar meaning), merge the weaker one into the stronger using dream_merge_memories.\n"
            "Use combined, cleaner language for the merged result.\n\n"
            "## Phase 3 — Resolve contradictions\n"
            "Look for memories that contradict each other (e.g., two conflicting facts about the same topic).\n"
            "Update the outdated one with dream_update_memory, or delete it if fully superseded.\n\n"
            "## Phase 4 — Improve phrasing\n"
            "Memories with vague, incomplete, or time-relative language (\"yesterday\", \"recently\", \"soon\") should be rewritten\n"
            "with absolute dates or clearer wording. Use dream_update_memory.\n\n"
            "## Phase 5 — Prune\n"
            "Delete memories that are: clearly wrong, fully superseded, trivially unimportant, or irrelevant noise.\n"
            "Always provide a reason when calling dream_delete_memory.\n\n"
            "## Phase 6 — Report\n"
            "Finish with a plain-text summary:\n"
            "- N memories merged (list the kept IDs)\n"
            "- N memories deleted (list the reasons briefly)\n"
            "- N memories updated (what changed)\n"
            "- Any notable patterns observed\n\n"
            "Be thorough but efficient. Do not delete anything you're uncertain about — when in doubt, update instead of delete.\n"
            "The goal is a cleaner, more accurate, non-redundant memory graph."
        )

        logging.info("[dream] Dream starting...")
        start = time.time()

        try:
            cfg = g_data.get("cfg")
            if not cfg:
                logging.warning("[dream] No config — aborting dream")
                return

            provider_name = self._dream_provider or self._fallback_provider
            model_name = self._dream_model or self._fallback_model
            provider_config = cfg.data.get("providers", {}).get(provider_name, {})

            provider = ProviderRegistry.get_provider(provider_name, provider_config)

            ctx = await ToolContext.for_heartbeat("dream")

            messages = [
                Message(role="system", content=_DREAM_SYSTEM_PROMPT),
                Message(role="user", content="Begin the dream. Start with Phase 1."),
            ]

            response = await provider.chat(messages, ctx.schemas, model=model_name)

            if response.tool_calls:
                response, _tool_msgs = await execute_tool_loop(
                    provider=provider,
                    messages=messages,
                    tools=ctx.tools,
                    model=model_name,
                    initial_response=response,
                    max_calls=self._max_tool_calls,
                )

            elapsed = time.time() - start
            summary = (response.content or "").strip()
            logging.info(
                f"[dream] Dream completed in {elapsed:.1f}s. Summary: {summary[:200]}"
            )
            # Mark completion only after success so a crash mid-dream
            # doesn't skip unprocessed memories on next restart.
            await self._set_state("last_dream_at", time.time())

            event_bus = g_data.get("event_bus")
            if event_bus:
                from lib.services.event_bus import Event
                # Always reset idle timers so she doesn't immediately re-dream —
                # even an empty-summary dream counts as activity.
                await event_bus.publish(Event("activity.recorded", {}))
                # Only surface to chat context if there's actually something to say.
                if summary:
                    await event_bus.publish(Event("task.completed", {
                        "task_type": "dream",
                        "title": "Memory Dream",
                        "summary": summary,
                    }))

        except Exception as e:
            logging.error(f"[dream] Dream failed: {e}", exc_info=True)
