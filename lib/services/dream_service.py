"""
dream_service.py — Auto-Dream: Nami reflects on her memory graph during downtime.

When Nami has been quiet for at least `min_idle_hours` AND at least
`min_new_memories` new memories have been created since the last dream,
a background AI agent wakes up and curates the memory graph:

  Phase 1 — Orient:       Read stats, review newest memories.
  Phase 2 — Deduplicate:  Find and merge near-duplicates.
  Phase 3 — Contradict:   Fix memories contradicted by newer ones.
  Phase 4 — Promote:      Elevate frequently-useful or important memories.
  Phase 5 — Prune:        Delete stale, low-value, clearly wrong memories.
  Phase 6 — Report:       Summarise what changed.

The dream agent uses the dream_tools.py toolset (never loaded for normal Nami).
By default it uses the memory extraction model, but can be configured to use a
more capable model via dream.provider / dream.model in config.yml.

No wake-up cancellation: if a user messages Nami while she's dreaming, she
responds normally — the dream continues in the background. The next dream run
will clean up any minor duplication caused by the overlap.
"""

import asyncio
import logging
import time

_DREAM_SYSTEM_PROMPT = """You are performing a Dream — a quiet, reflective pass over your own memory graph.
Your purpose is to curate and improve your memories so future conversations start with cleaner, more accurate context.

You have access to seven tools: dream_get_stats, dream_list_memories, dream_search_memories,
dream_get_memory, dream_update_memory, dream_delete_memory, dream_merge_memories.

Work through these phases in order:

## Phase 1 — Orient
Call dream_get_stats to see what you're working with.
Call dream_list_memories to review the newest memories (limit=30). Get a feel for recent activity.

## Phase 2 — Deduplicate
For each new memory, call dream_search_memories with its core content.
If you find a near-duplicate (very similar meaning), merge the weaker one into the stronger using dream_merge_memories.
Use combined, cleaner language for the merged result.

## Phase 3 — Resolve contradictions
Look for memories that contradict each other (e.g., two conflicting facts about the same topic).
Update the outdated one with dream_update_memory, or delete it if fully superseded.

## Phase 4 — Improve phrasing
Memories with vague, incomplete, or time-relative language ("yesterday", "recently", "soon") should be rewritten
with absolute dates or clearer wording. Use dream_update_memory.

## Phase 5 — Prune
Delete memories that are: clearly wrong, fully superseded, trivially unimportant, or irrelevant noise.
Always provide a reason when calling dream_delete_memory.

## Phase 6 — Report
Finish with a plain-text summary:
- N memories merged (list the kept IDs)
- N memories deleted (list the reasons briefly)
- N memories updated (what changed)
- Any notable patterns observed

Be thorough but efficient. Do not delete anything you're uncertain about — when in doubt, update instead of delete.
The goal is a cleaner, more accurate, non-redundant memory graph."""


class DreamService:
    """
    Background service that periodically runs a memory consolidation dream.

    Checks gates every 30 minutes. When all gates pass, spawns a dream agent
    as a background asyncio Task. Multiple dream runs cannot overlap (SQLite lock).
    """

    _POLL_INTERVAL = 30 * 60  # 30 minutes between gate checks

    def __init__(self, config, db_path: str = "scheduler.db"):
        """
        Args:
            config:  Full application config dict (from config.yml).
            db_path: Path to the SQLite DB used for dream state (shares scheduler.db).
        """
        self.config = config
        self.db_path = db_path
        mem_cfg = config.data.get("memory", {})
        self._dream_cfg = config.data.get("dream", {})
        self._min_idle_hours: float = self._dream_cfg.get("min_idle_hours", 2.0)
        self._min_new_memories: int = self._dream_cfg.get("min_new_memories", 5)
        self._max_tool_calls: int = self._dream_cfg.get("max_tool_calls", 40)
        # Optional dream-specific model config — falls back to extraction model if unset
        self._dream_provider: str | None = self._dream_cfg.get("provider")
        self._dream_model: str | None = self._dream_cfg.get("model")
        self._fallback_provider: str = mem_cfg.get("extraction_provider", "ollama")
        self._fallback_model: str = mem_cfg.get("extraction_model", "llama3.2")
        self._task: asyncio.Task | None = None
        self._active_dream: asyncio.Task | None = None
        self._last_message_at: float = time.time()

        logging.info(
            f"[dream] DreamService initialized — idle_hours={self._min_idle_hours}, "
            f"min_new_memories={self._min_new_memories}, max_tool_calls={self._max_tool_calls}, "
            f"dream_model={self._dream_provider or self._fallback_provider}/{self._dream_model or self._fallback_model}"
        )

    async def start(self) -> None:
        """Start the background poll loop."""
        await self._init_db()
        self._task = asyncio.create_task(self._poll_loop(), name="dream_poll")
        logging.info("[dream] Dream service started")

    async def stop(self) -> None:
        """Stop the poll loop (does not interrupt an in-progress dream)."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logging.info("[dream] Dream service stopped")

    def record_activity(self) -> None:
        """
        Call this on every incoming message to update last_message_at.
        Used by the idle gate — if Nami is active, don't dream.
        """
        self._last_message_at = time.time()

    # ------------------------------------------------------------------
    # Internal
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
        # Restore last_message_at from DB so idle timer survives restarts.
        # If nothing stored yet, default to now (conservative — skip dreaming right after a fresh start).
        stored = await self._get_state("last_message_at", default=0.0)
        self._last_message_at = stored if stored > 0 else time.time()

    async def _get_state(self, key: str, default: float = 0.0) -> float:
        """Read a float value from dream_state table."""
        import aiosqlite
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT value FROM dream_state WHERE key = ?", (key,)) as cur:
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

    async def _poll_loop(self) -> None:
        """Poll every 30 minutes and trigger a dream if all gates pass."""
        while True:
            try:
                await asyncio.sleep(self._POLL_INTERVAL)
                await self._maybe_dream()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[dream] Poll loop error: {e}", exc_info=True)

    async def _maybe_dream(self) -> None:
        """Check all gates and start a dream if conditions are met."""
        from lib.global_registry import g_data

        # Gate 1: dream enabled in config?
        if not self._dream_cfg.get("enabled", True):
            return

        # Gate 2: idle long enough?
        # Persist current in-memory activity time so it survives restarts.
        current_activity = getattr(self, "_last_message_at", 0.0)
        await self._set_state("last_message_at", current_activity)

        idle_seconds = time.time() - current_activity
        idle_hours = idle_seconds / 3600
        if idle_hours < self._min_idle_hours:
            logging.debug(f"[dream] Idle gate: {idle_hours:.1f}h < {self._min_idle_hours}h required")
            return

        # Gate 3: enough new memories since last dream?
        last_dream_at = await self._get_state("last_dream_at", default=0.0)
        memory_db = g_data.get("memory_db")
        if memory_db:
            new_count = await self._count_new_memories(memory_db, last_dream_at)
            if new_count < self._min_new_memories:
                logging.debug(f"[dream] Memory gate: {new_count} new memories < {self._min_new_memories} required")
                return
        else:
            logging.debug("[dream] memory_db not available — skipping dream")
            return

        # Gate 4: no dream already running?
        if self._active_dream and not self._active_dream.done():
            logging.debug("[dream] Dream already in progress — skipping")
            return

        logging.info(f"[dream] All gates passed — idle={idle_hours:.1f}h, new_memories={new_count}. Starting dream.")
        await self._set_state("last_dream_at", time.time())
        self._active_dream = asyncio.create_task(self._run_dream(), name="dream_agent")

    async def _count_new_memories(self, memory_db, since_timestamp: float) -> int:
        """Count memories created after `since_timestamp` (epoch seconds)."""
        try:
            # creationTimestamp is stored as epoch milliseconds in Neo4j.
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
        """Run the dream agent — one focused AI call with dream tools."""
        from lib.global_registry import g_data
        from lib.ai_providers import Message, ProviderRegistry
        from lib.services.tool_executor import execute_tool_loop
        from OllamaTools.dream_tools import get_tool as get_dream_tools

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

            # Build dream tools list
            dream_tools = list(get_dream_tools())

            # Sanitized tools for the provider (no func/safe keys)
            provider_tools = [
                {k: v for k, v in t.items() if k not in ("func", "safe")}
                for t in dream_tools
            ]

            messages = [
                Message(role="system", content=_DREAM_SYSTEM_PROMPT),
                Message(role="user", content="Begin the dream. Start with Phase 1."),
            ]

            response = await provider.chat(messages, provider_tools, model=model_name)

            if response.tool_calls:
                response, _tool_msgs = await execute_tool_loop(
                    provider=provider,
                    messages=messages,
                    tools=dream_tools,
                    model=model_name,
                    initial_response=response,
                    max_calls=self._max_tool_calls,
                )

            elapsed = time.time() - start
            summary = (response.content or "").strip()
            logging.info(f"[dream] Dream completed in {elapsed:.1f}s. Summary: {summary[:200]}")

        except Exception as e:
            logging.error(f"[dream] Dream failed: {e}", exc_info=True)
