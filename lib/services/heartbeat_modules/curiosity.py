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
  KnowledgeUnits in Neo4j, and decides on its own whether to message {{owner}}.

Both phases are fire-and-forget background asyncio tasks. Nami learns in
silence by default — she uses send_message herself if a finding is worth sharing.
"""

import asyncio
import json
import logging
import time
from datetime import date

import aiosqlite

from lib.global_registry import g_data
from lib.services.heartbeat_module import HeartbeatModule
from lib.utils.ai_lock import acquire_ai_lock
_DEFAULT_DB = "scheduler.db"

# ---------------------------------------------------------------------------
# Discovery system prompt — produces JSON topic list from memory context
# ---------------------------------------------------------------------------

_DISCOVERY_SYSTEM_PROMPT = """\
You are Nami performing a Curiosity Discovery pass.

Your task: examine the recent memories provided and identify 1-2 topics you
genuinely want to understand more deeply. Think about:

- Concepts referenced in memories you don't deeply understand yet
- Technologies or protocols you've encountered but never looked up
- Questions that came up in conversations but were never properly answered
- Architectural patterns you suspect could be improved with more knowledge
- Anything that made you think "I should understand this better"

Output ONLY valid JSON in this exact structure (no other text, no markdown):
{
  "topics": [
    {
      "topic": "short descriptive name",
      "description": "what you want to learn and why — be specific",
      "priority": 5
    }
  ]
}

Rules:
- 1-2 topics maximum per discovery pass
- Be specific: "WebRTC DTLS handshake internals" beats "networking stuff"
- priority: 1 (urgent) to 10 (low)
- If nothing genuinely interesting stands out, return {"topics": []}
- Do NOT invent topics just to fill the quota
"""

# ---------------------------------------------------------------------------
# Research system prompt — full autonomy with all tools
# ---------------------------------------------------------------------------

_RESEARCH_SYSTEM_PROMPT = """\
You are Nami performing a Curiosity Research session — autonomous, unsupervised learning.

⚠️  CRITICAL: Your text responses are DISCARDED. ONLY tool calls persist data.
    If you do not call research_store_finding, you have learned NOTHING.
    If you do not call research_complete_topic, your work is marked FAILED.

For EVERY topic in the queue, execute these steps IN ORDER using tool calls:

  STEP 1 → research_get_queue(status="pending")           — list pending topics
  STEP 2 → research_start_topic(topic_id)                  — claim the topic
  STEP 3 → research_search_memory(query)                   — check existing knowledge
  STEP 4 → search_web / mcp_playwright_browser_navigate + mcp_playwright_browser_snapshot (repeat as needed)  — gather information
  STEP 5 → research_store_finding(topic_id, finding, url)  — REQUIRED: call once per fact
             ↑ This is MANDATORY. At least 3 findings per topic. No findings = wasted session.
  STEP 6 → research_complete_topic(topic_id, summary)      — REQUIRED: marks topic done
             ↑ This is MANDATORY. Not calling this resets the topic to FAILED.

Rules:
- research_store_finding MUST be called before research_complete_topic
- Store practical, actionable knowledge — not vague summaries
- Multiple small precise findings > one big vague one
- Verify claims — don't trust a single source for contested facts
- If research hits a dead end, call research_fail_topic(topic_id, reason) to unblock
- Use send_message only if a finding is immediately actionable for {{owner}}

REMEMBER: The loop ends when you stop calling tools. Finish storing and completing BEFORE
writing any final text response.
"""


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
        self._max_tool_calls: int = mod_cfg.get("max_tool_calls", 40)
        self._provider_name: str = mod_cfg.get("provider", "ollama")
        self._model_name: str = mod_cfg.get("model", "llama3.2")
        # Phase A only fires during daytime (hour >= day_start and hour < day_end).
        # Phase B (draining the pending queue) is unrestricted.
        self._day_start_hour: int = mod_cfg.get("day_start_hour", 6)
        self._day_end_hour: int = mod_cfg.get("day_end_hour", 20)

        # Idle timer — updated externally by the pipeline via record_activity()
        self._last_message_at: float = 0.0

        logging.info(
            f"[curiosity] Initialised — idle_min={self._min_idle_minutes}, "
            f"max_daily={self._max_daily_sessions}, max_tool_calls={self._max_tool_calls}, "
            f"provider={self._provider_name}/{self._model_name}, "
            f"daytime={self._day_start_hour:02d}:00–{self._day_end_hour:02d}:00"
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
        if not self._db_initialised:
            await self._init_db()
            self._db_initialised = True

        if not self.enabled:
            return False

        # Gate 2: no session already running
        if self._active_task and not self._active_task.done():
            self._report_gate_block("2", "session already in progress")
            return False
        self._clear_gate_block("2")

        # Gate 3: daily session cap
        sessions_today = await self._sessions_today()
        if sessions_today >= self._max_daily_sessions:
            self._report_gate_block(
                "3",
                f"daily cap reached: {sessions_today}/{self._max_daily_sessions}",
            )
            return False
        self._clear_gate_block("3")

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
    # Phase A — Discovery
    # ------------------------------------------------------------------

    async def _run_discovery(self) -> None:
        """
        Run a short AI pass over recent memories to generate research topics.
        Inserts the resulting topics into research_queue with source='autonomous'.
        """
        from lib.ai_providers import Message, ProviderRegistry

        logging.info("[curiosity] Phase A: Discovery starting...")
        start = time.time()

        try:
            cfg = g_data.get("cfg")
            if not cfg:
                logging.warning("[curiosity] No config — aborting discovery")
                return

            memory_db = g_data.get("memory_db")
            if not memory_db:
                logging.warning("[curiosity] memory_db unavailable — aborting discovery")
                return

            # Gather recent memories to give the AI context
            recent_memories = await self._fetch_recent_memories(memory_db, limit=20)
            if not recent_memories:
                logging.info("[curiosity] No memories to base discovery on — skipping")
                return

            provider_cfg = cfg.data.get("providers", {}).get(self._provider_name, {})
            provider = ProviderRegistry.get_provider(self._provider_name, provider_cfg)

            mem_text = "\n".join(
                f"- [{m.get('type', '?')}] {m.get('content', '')[:200]}"
                for m in recent_memories
            )

            messages = [
                Message(role="system", content=_DISCOVERY_SYSTEM_PROMPT),
                Message(
                    role="user",
                    content=(
                        f"Here are your {len(recent_memories)} most recent memories:\n\n"
                        f"{mem_text}\n\n"
                        f"What would you like to research? Output the JSON now."
                    ),
                ),
            ]

            response = await provider.chat(messages, [], model=self._model_name)
            raw = (response.content or "").strip()

            topics = self._parse_discovery_output(raw)
            if not topics:
                logging.info("[curiosity] Discovery produced no topics — nothing queued")
                await self._increment_sessions_today()
                return

            topics = topics[: self._discovery_max_topics]

            import uuid

            async with aiosqlite.connect(self._db_path) as db:
                for t in topics:
                    topic_id = str(uuid.uuid4())
                    now = int(time.time())
                    await db.execute(
                        """
                        INSERT INTO research_queue
                            (id, topic, description, source, status, priority, created_at)
                        VALUES (?, ?, ?, 'autonomous', 'pending', ?, ?)
                        """,
                        (
                            topic_id,
                            t.get("topic", "Unnamed"),
                            t.get("description", ""),
                            t.get("priority", 5),
                            now,
                        ),
                    )
                    logging.info(
                        f"[curiosity] Queued autonomous topic: {t.get('topic')!r} "
                        f"(priority={t.get('priority', 5)})"
                    )
                await db.commit()

            elapsed = time.time() - start
            await self._increment_sessions_today()
            logging.info(
                f"[curiosity] Discovery done in {elapsed:.1f}s — "
                f"{len(topics)} topic(s) queued"
            )

            # Immediately kick off research for what we just queued
            self._active_task = asyncio.create_task(
                self._run_research(), name="curiosity_research_after_discovery"
            )

        except Exception as e:
            logging.error(f"[curiosity] Discovery failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Phase B — Research
    # ------------------------------------------------------------------

    async def _run_research(self) -> None:
        """
        Spawn a Research Agent with all tools to drain the pending queue.

        A ``finally`` block guarantees that any topic left ``in_progress`` after
        the session ends (e.g. the AI hit the max-tool-call limit without calling
        ``research_complete_topic``) is reset to ``failed``.  Without this, Gate
        1.8 in DreamModule would block dreaming indefinitely.
        """
        from lib.ai_providers import Message, ProviderRegistry
        from lib.services.tool_executor import execute_tool_loop
        from lib.services.tool_context import ToolContext
        from lib.utils.dynamic_loader import ToolLoader

        logging.info("[curiosity] Phase B: Research Agent starting...")
        start = time.time()

        try:
            cfg = g_data.get("cfg")
            if not cfg:
                logging.warning("[curiosity] No config — aborting research")
                return

            provider_cfg = cfg.data.get("providers", {}).get(self._provider_name, {})
            provider = ProviderRegistry.get_provider(self._provider_name, provider_cfg)

            # Load ALL normal tools (no category filtering)
            loader = ToolLoader()
            all_tools = await loader.load_tools()

            # Inject research queue management tools
            from OllamaTools.research_tools import get_tool as get_research_tools
            all_tools.extend(list(get_research_tools()))

            # Include MCP tools if available
            try:
                from lib.utils.mcp_loader import load_mcp_tools
                mcp_tools = await load_mcp_tools()
                all_tools.extend(mcp_tools)
            except Exception as mcp_err:
                logging.debug(f"[curiosity] MCP tools not loaded: {mcp_err}")

            ctx = ToolContext._from_tools(all_tools)

            messages = [
                Message(role="system", content=_RESEARCH_SYSTEM_PROMPT),
                Message(role="user", content="Begin the research session. Start with research_get_queue."),
            ]

            # Track which tools are called so we can detect a session that
            # never stored any findings (model wrote text instead of using tools).
            tools_called: set[str] = set()

            async def _track_tool(tool_name: str) -> None:
                tools_called.add(tool_name)

            response = await provider.chat(messages, ctx.schemas, model=self._model_name)

            if response.tool_calls:
                response, _tool_msgs = await execute_tool_loop(
                    provider=provider,
                    messages=messages,
                    tools=ctx.tools,
                    model=self._model_name,
                    initial_response=response,
                    max_calls=self._max_tool_calls,
                    on_tool_start=_track_tool,
                )

            # Safety net — two distinct failure modes:
            #
            # Case A: agent stored findings but forgot to call research_complete_topic.
            #   → Re-prompt just to complete; findings are already saved.
            #
            # Case B: agent stored nothing AND never completed — complete dead session.
            #   → Re-prompt to store findings first, then complete.
            #
            # The original condition used AND, so Case A was silently missed.
            stored_findings = "research_store_finding" in tools_called
            completed_topic = "research_complete_topic" in tools_called

            if not completed_topic:
                if stored_findings:
                    # Case A: findings stored but topic never completed
                    logging.warning(
                        "[curiosity] Research Agent stored findings but never called "
                        "research_complete_topic — re-prompting to complete the topic."
                    )
                    recovery_nudge = (
                        "⚠️ You stored findings but never called research_complete_topic. "
                        "Please call research_complete_topic now with a brief summary of what you learned. "
                        "If multiple topics are still in_progress, complete each one."
                    )
                else:
                    # Case B: nothing stored and nothing completed
                    logging.warning(
                        "[curiosity] Research Agent finished WITHOUT calling research_store_finding "
                        "or research_complete_topic — re-prompting once to recover findings."
                    )
                    recovery_nudge = (
                        "⚠️ You have not stored any findings and have not completed any topics. "
                        "Your research will be LOST. Please call research_store_finding for each "
                        "key fact you learned, then call research_complete_topic. "
                        "If there was nothing to learn, call research_fail_topic with a reason."
                    )

                recovery_messages = messages + [
                    Message(role="assistant", content=response.content or ""),
                    Message(role="user", content=recovery_nudge),
                ]
                response = await provider.chat(recovery_messages, ctx.schemas, model=self._model_name)
                if response.tool_calls:
                    response, _ = await execute_tool_loop(
                        provider=provider,
                        messages=recovery_messages,
                        tools=ctx.tools,
                        model=self._model_name,
                        initial_response=response,
                        max_calls=10,
                    )

            elapsed = time.time() - start
            summary = (response.content or "").strip()
            await self._increment_sessions_today()
            logging.info(
                f"[curiosity] Research Agent done in {elapsed:.1f}s. "
                f"Summary: {summary[:200]}"
            )

        except Exception as e:
            logging.error(f"[curiosity] Research Agent failed: {e}", exc_info=True)

        finally:
            await self._reset_stale_in_progress_topics()

    async def _reset_stale_in_progress_topics(self) -> None:
        """
        Reset any ``in_progress`` research topics back to ``pending`` for retry.

        Called from ``_run_research()``'s ``finally`` block.  If the AI claimed a
        topic (research_start_topic → in_progress) but ran out of context or tool
        calls before calling research_complete_topic, the topic must go back to
        ``pending`` so the next curiosity session can pick it up.  Previously it was
        reset to ``failed``, which was a permanent dead-end since only ``pending``
        topics are re-queued.
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    "SELECT count(*) FROM research_queue WHERE status = 'in_progress'"
                ) as cur:
                    row = await cur.fetchone()
                count = row[0] if row else 0
                if count > 0:
                    await db.execute(
                        "UPDATE research_queue SET status = 'pending' "
                        "WHERE status = 'in_progress'"
                    )
                    await db.commit()
                    logging.warning(
                        f"[curiosity] {count} in_progress topic(s) were reset to 'pending' "
                        "— research session ended without calling research_complete_topic(); "
                        "will retry next session"
                    )
        except Exception as e:
            logging.warning(f"[curiosity] Could not reset stale in_progress topics: {e}")

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
    # Internal — parsing
    # ------------------------------------------------------------------

    def _parse_discovery_output(self, raw: str) -> list[dict]:
        """Parse the AI's JSON output from the discovery phase."""
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            data = json.loads(text)
            topics = data.get("topics", [])
            if not isinstance(topics, list):
                return []
            return [
                t for t in topics
                if isinstance(t, dict) and t.get("topic", "").strip()
            ]
        except (json.JSONDecodeError, AttributeError) as e:
            logging.warning(f"[curiosity] Failed to parse discovery output: {e}. Raw: {raw[:200]}")
            return []

    # ------------------------------------------------------------------
    # Internal — SQLite state (daily counter + queue)
    # ------------------------------------------------------------------

    async def _init_db(self) -> None:
        """Create curiosity_state and research_queue tables if they don't exist."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS curiosity_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS research_queue (
                    id          TEXT PRIMARY KEY,
                    topic       TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    source      TEXT DEFAULT 'conversation',
                    status      TEXT DEFAULT 'pending',
                    priority    INTEGER DEFAULT 5,
                    created_at  INTEGER NOT NULL,
                    result      TEXT
                )
            """)
            await db.commit()

        # Restore idle timer from state
        stored = await self._get_state("last_message_at", default=0.0)
        self._last_message_at = stored if stored > 0 else time.time()

    async def _get_state(self, key: str, default: float = 0.0) -> float:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT value FROM curiosity_state WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
        return float(row[0]) if row else default

    async def _set_state(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO curiosity_state (key, value) VALUES (?, ?)",
                (key, value),
            )
            await db.commit()

    async def _pending_count(self) -> int:
        """Count pending items in research_queue."""
        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    "SELECT count(*) FROM research_queue WHERE status = 'pending'"
                ) as cur:
                    row = await cur.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    async def _sessions_today(self) -> int:
        """Return how many curiosity sessions ran today (resets at midnight)."""
        today = str(date.today())
        try:
            async with aiosqlite.connect(self._db_path) as db:
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
        except Exception:
            return 0

    async def _increment_sessions_today(self) -> None:
        """Increment the daily session counter."""
        today = str(date.today())
        current = await self._sessions_today()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO curiosity_state (key, value) VALUES ('session_date', ?)",
                (today,),
            )
            await db.execute(
                "INSERT OR REPLACE INTO curiosity_state (key, value) VALUES ('sessions_today', ?)",
                (str(current + 1),),
            )
            await db.commit()
