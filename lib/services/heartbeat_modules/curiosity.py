"""
curiosity.py — HeartbeatModule: Nami's autonomous learning engine.

Nami runs in two phases:

Phase A — Discovery (runs when idle, no pending queue):
  A lightweight AI pass over recent memories produces 1-2 research topics
  Nami decided herself she wants to understand better. These are inserted into
  the research_queue with source='autonomous'.

Phase B — Research (runs when research_queue has pending items):
  A full Research Agent with ALL tools (web search, sandbox, memory read/write,
  send_message, etc.) investigates each pending topic, stores findings as
  KnowledgeUnits in Neo4j, and decides on its own whether to message the owner.

Both phases are fire-and-forget background asyncio tasks. Nami learns in
silence by default — she uses send_message herself if a finding is worth sharing.

Implementation is split across:
  curiosity_discovery.py  — Phase A: Discovery prompts, AI call, topic parsing
  curiosity_research.py   — Phase B: Research agent, tool loop, recovery
  curiosity.py (here)     — HeartbeatModule glue, queue management, state tracking
"""

import asyncio
import logging
import time
from datetime import date

import aiosqlite

from lib.global_registry import g_data
from lib.services.heartbeat_module import HeartbeatModule
from lib.services.heartbeat_modules.curiosity_discovery import run_discovery
from lib.services.heartbeat_modules.curiosity_research import run_research
from lib.utils import resolve_provider_model
from lib.utils.ai_lock import acquire_ai_lock
from lib.utils.sqlite_kv import SqliteKVStore
from OllamaTools.queue_research import _CREATE_TABLE


class CuriosityModule(HeartbeatModule):
    """
    Nami's autonomous learning engine.

    Gates (checked in condition()):
    1. Module enabled in config?
    2. Not already running a session?
    3. Daily session cap not exceeded?
    4. Pending research queue items? (→ Phase B) OR idle long enough? (→ Phase A)

    action() runs the appropriate phase as a background task.
    """

    name = "curiosity"
    priority = 20
    cooldown_seconds = 1800  # default: 30 minutes (config overrides)

    def __init__(self, config=None, db_path: str = "scheduler.db") -> None:
        super().__init__()
        self._config = config
        self._db_path = db_path
        self._state = SqliteKVStore(self._db_path, "curiosity_state")
        self._db_initialised: bool = False
        self._active_task: asyncio.Task | None = None

        hb_cfg = config.data.get("heartbeat", {}) if config else {}
        mod_cfg = hb_cfg.get("modules", {}).get("curiosity", {})

        self.enabled = mod_cfg.get("enabled", True)
        if "cooldown" in mod_cfg:
            self.cooldown_seconds = mod_cfg["cooldown"]

        self._min_idle_minutes: float = mod_cfg.get("min_idle_minutes", 30)
        self._max_daily_sessions: int = mod_cfg.get("max_daily_sessions", 3)
        self._discovery_max_topics: int = mod_cfg.get("discovery_max_topics", 2)
        self._max_tool_calls: int = mod_cfg.get("max_tool_calls", 5)
        self._max_tool_rounds: int = mod_cfg.get("max_tool_rounds", 10)
        self._provider_name, self._model_name = resolve_provider_model(
            mod_cfg.get("model"),
            fallback_provider=mod_cfg.get("provider", "ollama"),
            fallback_model="llama3.2",
        )
        # Phase A only fires during daytime (hour >= day_start and hour < day_end).
        # Phase B (draining the pending queue) is unrestricted.
        self._day_start_hour: int = mod_cfg.get("day_start_hour", 6)
        self._day_end_hour: int = mod_cfg.get("day_end_hour", 20)
        self._retry_max_attempts: int = mod_cfg.get("retry_max_attempts", 3)
        self._retry_backoff_base_hours: int = mod_cfg.get("retry_backoff_base_hours", 1)

        # Idle timer — updated externally by the pipeline via record_activity()
        self._last_message_at: float = 0.0

        logging.info(
            f"[curiosity] Initialised — idle_min={self._min_idle_minutes}, "
            f"max_daily={self._max_daily_sessions}, max_tool_calls={self._max_tool_calls}, "
            f"provider={self._provider_name}/{self._model_name}, "
            f"daytime={self._day_start_hour:02d}:00–{self._day_end_hour:02d}:00, "
            f"retry_max={self._retry_max_attempts}, backoff_base={self._retry_backoff_base_hours}h"
        )

    def record_activity(self) -> None:
        """Call on every incoming message to reset the idle timer."""
        self._last_message_at = time.time()

    def _is_daytime(self) -> bool:
        """Return True if the current local hour is within the configured daytime window."""
        import datetime
        hour = datetime.datetime.now().hour
        return self._day_start_hour <= hour < self._day_end_hour

    # ------------------------------------------------------------------
    # Lifecycle — connection management
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open the persistent DB connection and initialize schema."""
        self._conn = await aiosqlite.connect(self._db_path)
        self._state._conn = self._conn
        await self._init_db()
        logging.info("[curiosity] CuriosityModule started (db=%s)", self._db_path)

    async def stop(self) -> None:
        """Close the DB connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
        logging.info("[curiosity] CuriosityModule stopped")

    # ------------------------------------------------------------------
    # HeartbeatModule interface
    # ------------------------------------------------------------------

    async def condition(self) -> bool:
        """
        Return True if a curiosity session should start.

        Gates (checked in order):
        1. Enabled?
        2. Not already running?
        3. Daily cap not exceeded?
        4. Phase B: pending topics → fire immediately.
           Phase A: idle long enough AND within daytime window.
        """
        if not self.enabled:
            return False

        # Gate 2: no session already running
        if self._active_task and not self._active_task.done():
            self._report_gate_block("2", "session already in progress")
            return False
        self._clear_gate_block("2")

        # Lazy DB init: if start() failed or was never called, init now
        if not self._db_initialised:
            try:
                await self._init_db()
            except Exception as e:
                logging.error("[curiosity] Lazy DB init failed, skipping condition check: %s", e)
                return False

        # Gate 3: daily session cap
        sessions_today = await self._sessions_today()
        if sessions_today >= self._max_daily_sessions:
            self._report_gate_block(
                "3",
                f"daily cap reached: {sessions_today}/{self._max_daily_sessions}",
            )
            return False
        self._clear_gate_block("3")

        # Promote any failed_retry topics whose backoff has expired
        await self._promote_retry_topics()

        # Gate 4 — Phase B: pending topics bypass the idle/daytime check
        pending = await self._pending_count()
        if pending > 0:
            self._clear_gate_block("4")
            logging.info(f"[curiosity] {pending} pending topic(s) → triggering research (Phase B)")
            return True

        # Gate 4 — Phase A: idle long enough AND within daytime window
        idle_secs = time.time() - self._last_message_at if self._last_message_at > 0 else 0
        idle_minutes = idle_secs / 60
        if idle_minutes < self._min_idle_minutes:
            self._report_gate_block(
                "4",
                f"idle only {idle_minutes:.1f}min, need {self._min_idle_minutes}min",
            )
            return False

        if not self._is_daytime():
            import datetime
            hour = datetime.datetime.now().hour
            self._report_gate_block(
                "4",
                f"idle {idle_minutes:.1f}min sufficient but hour={hour:02d} outside daytime "
                f"{self._day_start_hour:02d}:00–{self._day_end_hour:02d}:00",
                log_interval=7200.0,
            )
            return False

        self._clear_gate_block("4")
        logging.info(
            f"[curiosity] All gates passed — idle={idle_minutes:.1f}min "
            f">= {self._min_idle_minutes}min → triggering discovery (Phase A)"
        )
        return True

    async def action(self) -> None:
        """Schedule the appropriate phase behind the shared AI lock.

        Queues behind any in-progress chat message or dream session naturally —
        the lock is the single source of truth for mutual exclusion.
        """
        pending = await self._pending_count()
        target_coro = self._run_research() if pending > 0 else self._run_discovery()
        target_name = "curiosity_research" if pending > 0 else "curiosity_discovery"

        async def _locked_run() -> None:
            lock = g_data.get("ai_lock")
            if lock:
                if not await acquire_ai_lock(lock, label=target_name):
                    logging.error("[curiosity] Lock holder inactive — skipping session")
                    return
                try:
                    await target_coro
                finally:
                    lock.release()
            else:
                await target_coro  # fallback: lock not ready yet

        self._active_task = asyncio.create_task(_locked_run(), name=target_name)

    # ------------------------------------------------------------------
    # Phase A — Discovery (delegated to curiosity_discovery.py)
    # ------------------------------------------------------------------

    async def _run_discovery(self) -> None:
        """Orchestrate Discovery phase — delegated to curiosity_discovery module."""
        await run_discovery(self)

    # ------------------------------------------------------------------
    # Phase B — Research (delegated to curiosity_research.py)
    # ------------------------------------------------------------------

    async def _run_research(self) -> None:
        """Orchestrate Research phase — delegated to curiosity_research module."""
        await run_research(self)

    # ------------------------------------------------------------------
    # Internal — queue introspection for topic deduplication
    # ------------------------------------------------------------------

    async def _fetch_recent_queue_topics(self) -> dict[str, list[dict]]:
        """Return recently completed and in-progress topics from research_queue.

        Used by the Discovery pass to avoid re-queuing topics that were just
        finished or are currently being researched.
        """
        result: dict[str, list[dict]] = {"completed": [], "in_progress": []}
        try:
            db = await self._ensure_conn()
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT topic, description, status, created_at "
                "FROM research_queue "
                "WHERE status = 'done' "
                "ORDER BY created_at DESC LIMIT 3"
            ) as cur:
                rows = await cur.fetchall()
                result["completed"] = [dict(r) for r in rows]

            async with db.execute(
                "SELECT topic, description, status, created_at "
                "FROM research_queue "
                "WHERE status = 'in_progress'"
            ) as cur:
                rows = await cur.fetchall()
                result["in_progress"] = [dict(r) for r in rows]
        except Exception as e:
            logging.warning(f"[curiosity] Failed to fetch recent queue topics: {e}")
        return result

    # ------------------------------------------------------------------
    # Internal — memory fetching
    # ------------------------------------------------------------------

    async def _fetch_recent_memories(self, memory_db, limit: int = 20) -> list[dict]:
        """Fetch recent memories as plain dicts for the discovery prompt."""
        results = []
        try:
            driver = memory_db.get_driver()
            field_map = {
                "EpisodicMemory": "summary",
                "KnowledgeUnit": "statement",
                "ProceduralUnit": "description",
            }
            async with driver.session() as session:
                for label, field in field_map.items():
                    res = await session.run(
                        f"""
                        MATCH (m:{label})
                        RETURN m.{field} AS content, '{label}' AS type
                        ORDER BY m.creationTimestamp DESC
                        LIMIT $lim
                        """,
                        {"lim": limit // len(field_map) + 1},
                    )
                    async for record in res:
                        content = record["content"]
                        if content:
                            results.append({"type": label, "content": content})
        except Exception as e:
            logging.warning(f"[curiosity] Failed to fetch recent memories: {e}")
        return results[:limit]

    # ------------------------------------------------------------------
    # Internal — SQLite state (daily counter + queue)
    # ------------------------------------------------------------------

    async def _init_db(self) -> None:
        """Create curiosity_state and research_queue tables if they don't exist."""
        db = await self._ensure_conn()
        await db.execute("""
            CREATE TABLE IF NOT EXISTS curiosity_state (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute(_CREATE_TABLE)
        # Migrate existing tables that don't have the retry columns yet
        for col, col_type in [("retry_count", "INTEGER DEFAULT 0"), ("next_retry_at", "INTEGER")]:
            try:
                await db.execute(f"ALTER TABLE research_queue ADD COLUMN {col} {col_type}")
            except aiosqlite.OperationalError:
                pass  # Column already exists — SQLite doesn't support IF NOT EXISTS for ALTER
        await db.commit()

        # Restore idle timer from state
        stored = await self._state.get("last_message_at", default=0.0)
        self._last_message_at = stored if stored > 0 else time.time()
        self._db_initialised = True

    async def _promote_retry_topics(self) -> None:
        """Move eligible ``failed_retry`` topics back to ``pending``.

        Topics whose ``next_retry_at`` has passed are reset to ``pending`` so the
        next research session picks them up.  Topics that have exhausted their
        retry budget are permanently marked ``failed``.
        """
        now = int(time.time())
        try:
            db = await self._ensure_conn()
            # Permanently fail topics that exceeded max retries
            await db.execute(
                "UPDATE research_queue SET status = 'failed' "
                "WHERE status = 'failed_retry' AND retry_count >= ?",
                (self._retry_max_attempts,),
            )
            # Promote expired retry topics back to pending
            await db.execute(
                "UPDATE research_queue SET status = 'pending' "
                "WHERE status = 'failed_retry' "
                "AND next_retry_at IS NOT NULL AND next_retry_at <= ?",
                (now,),
            )
            await db.commit()
        except Exception as e:
            logging.warning(f"[curiosity] Failed to promote retry topics: {e}")

    async def _pending_count(self) -> int:
        """Count pending items in research_queue (includes ready retry topics)."""
        try:
            db = await self._ensure_conn()
            async with db.execute(
                "SELECT count(*) FROM research_queue WHERE status = 'pending'"
            ) as cur:
                row = await cur.fetchone()
            return row[0] if row else 0
        except aiosqlite.OperationalError:
            return 0

    async def _sessions_today(self) -> int:
        """Return how many curiosity sessions ran today (resets at midnight)."""
        today = str(date.today())
        try:
            db = await self._ensure_conn()
            async with db.execute(
                "SELECT value FROM curiosity_state WHERE key = 'session_date'"
            ) as cur:
                row = await cur.fetchone()
            stored_date_str = row[0] if row else ""

            if stored_date_str != today:
                # New day — reset counter
                await db.execute(
                    "INSERT OR REPLACE INTO curiosity_state (key, value) VALUES ('session_date', ?)",
                    (today,),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO curiosity_state (key, value) VALUES ('sessions_today', '0')"
                )
                await db.commit()
                return 0

            async with db.execute(
                "SELECT value FROM curiosity_state WHERE key = 'sessions_today'"
            ) as cur:
                row = await cur.fetchone()
            return int(row[0]) if row else 0
        except aiosqlite.OperationalError:
            return 0

    async def _increment_sessions_today(self) -> None:
        """Increment the daily session counter atomically.

        Uses BEGIN IMMEDIATE to prevent read-modify-write races: the date
        check and increment happen in a single transaction so no concurrent
        call can read a stale counter value.
        """
        today = str(date.today())
        db = await self._ensure_conn()
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT value FROM curiosity_state WHERE key = 'session_date'"
        ) as cur:
            row = await cur.fetchone()
        stored_date = row[0] if row else ""

        if stored_date != today:
            # New day — reset counter to 1
            await db.execute(
                "INSERT OR REPLACE INTO curiosity_state (key, value) VALUES ('session_date', ?)",
                (today,),
            )
            await db.execute(
                "INSERT OR REPLACE INTO curiosity_state (key, value) VALUES ('sessions_today', '1')"
            )
        else:
            async with db.execute(
                "SELECT value FROM curiosity_state WHERE key = 'sessions_today'"
            ) as cur:
                row = await cur.fetchone()
            current = int(row[0]) if row else 0
            await db.execute(
                "INSERT OR REPLACE INTO curiosity_state (key, value) VALUES ('sessions_today', ?)",
                (str(current + 1),),
            )
        await db.commit()
