"""
memory_grooming.py — HeartbeatModule: AI-powered proactive memory review.

Runs periodically (default: every 2 hours, only when enough new memories).
Spawns an AI agent with dream_tools to:

1. Detect contradictions (same concept, conflicting facts)
2. Find near-duplicates for merge suggestions
3. Flag stale/decayed low-importance memories
4. Identify knowledge gaps (user patterns without details)

Results are logged in structured format (not raw AI text).
Auto-merge is configurable but off by default for safety.
"""

import asyncio
import json
import logging
import re
import time

from lib.global_registry import g_data
from lib.services.heartbeat_module import HeartbeatModule
from lib.utils.ai_lock import acquire_ai_lock
from lib.utils.sqlite_kv import SqliteKVStore

_GROOMING_SYSTEM_PROMPT = (
    "You are performing Memory Grooming — a quiet maintenance pass over Nami's memory graph.\n"
    "Your purpose is to find and report issues, NOT to chat. Be thorough but efficient.\n\n"
    "You have access to seven tools: dream_get_stats, dream_list_memories, dream_search_memories,\n"
    "dream_get_memory, dream_update_memory, dream_delete_memory, dream_merge_memories.\n\n"
    "Work through these phases in order:\n\n"
    "## Phase 1 — Orient\n"
    "Call dream_get_stats to see what you're working with.\n"
    "Call dream_list_memories with limit=30 to review recent memories.\n\n"
    "## Phase 2 — Detect Contradictions\n"
    "For each distinct concept or topic you see, call dream_search_memories with related queries.\n"
    "Look for two memories that state opposite facts about the same subject.\n"
    'Example: "User uses Python" vs "User hates Python and only uses Rust".\n'
    "Flag each contradiction with both memory IDs. Do NOT auto-resolve — just REPORT.\n\n"
    "## Phase 3 — Find Near-Duplicates\n"
    "For important-looking memories, call dream_search_memories with their core content.\n"
    "If you find two memories with very similar meaning (>90% overlap), flag them.\n"
    "Do NOT call dream_merge_memories unless auto-merge is explicitly enabled.\n"
    "The config tells you if auto-merge is enabled at the start.\n\n"
    "## Phase 4 — Flag Stale / Decayed\n"
    "Among the memories you reviewed, note any that appear:\n"
    "- No longer relevant (outdated info, past events fully resolved)\n"
    "- Very old and disconnected (no relationships to other memories)\n"
    "- Trivially unimportant (noise, greetings, one-word notes)\n"
    "Do NOT delete them — just flag with reason.\n\n"
    "## Phase 5 — Identify Knowledge Gaps\n"
    "Notice patterns in memories that reference something without defining it.\n"
    'Example: "User mentioned project X" but no memory defines what project X is.\n'
    'Example: "Config uses value Y" but no memory explains why Y was chosen.\n'
    "Flag each gap clearly.\n\n"
    "## Phase 6 — Structured Report\n"
    "When done, output a markdown report with these EXACT section headers:\n\n"
    "## CONTRADICTIONS\n"
    '(List each contradiction with both memory IDs and the conflicting facts, or "None found")\n\n'
    "## NEAR_DUPLICATES\n"
    '(List each pair with both IDs, similarity description, and merge suggestion, or "None found")\n\n'
    "## STALE_FLAGGED\n"
    '(List each stale memory with ID, type, and reason, or "None found")\n\n'
    "## KNOWLEDGE_GAPS\n"
    '(List each gap with the missing concept and what pattern suggested it, or "None found")\n\n'
    "## SUMMARY\n"
    "(One-line summary: total found in each category)\n\n"
    "Be thorough. A clean report saying 'None found' is better than missing issues."
)


class MemoryGrooming(HeartbeatModule):
    """AI-powered proactive memory graph hygiene checks."""

    name = "memory_grooming"
    priority = 50
    cooldown_seconds = 7200  # Default: every 2 hours (config overrides)

    def __init__(
        self,
        config=None,
        db_path: str = "scheduler.db",
    ) -> None:
        super().__init__()
        self._config = config
        self._db_path = db_path
        self._db_initialised: bool = False
        self._last_report: dict = {}
        self._state = SqliteKVStore(self._db_path, "grooming_state")

        # Parse per-module config
        hb_cfg = config.data.get("heartbeat", {}) if config else {}
        mod_cfg = hb_cfg.get("modules", {}).get("memory_grooming", {})

        self.enabled = mod_cfg.get("enabled", True)
        if "cooldown" in mod_cfg:
            self.cooldown_seconds = mod_cfg["cooldown"]

        self._min_new_memories: int = mod_cfg.get("min_new_memories", 5)
        self._provider_name: str = mod_cfg.get("provider", "ollama")
        self._model_name: str = mod_cfg.get("model", "llama3.2")
        self._max_tool_calls: int = mod_cfg.get("max_tool_calls", 8)
        self._max_tool_rounds: int = mod_cfg.get("max_tool_rounds", 10)
        self._auto_merge: bool = mod_cfg.get("auto_merge", False)

        logging.info(
            f"[memory_grooming] Initialised — min_new={self._min_new_memories}, "
            f"provider={self._provider_name}, model={self._model_name}, "
            f"max_tool_calls={self._max_tool_calls}, auto_merge={self._auto_merge}"
        )

    # ------------------------------------------------------------------
    # HeartbeatModule interface
    # ------------------------------------------------------------------

    async def condition(self) -> bool:
        """
        Return True if grooming should run.

        Gates (checked in order):
        1. memory_db available?
        2. Enough new memories since last grooming?
        """
        if not self._db_initialised:
            await self._init_db()
            self._db_initialised = True

        # Gate 1: memory_db must be available
        memory_db = g_data.get("memory_db")
        if not memory_db:
            self._report_gate_block("1", "memory_db not available")
            return False
        self._clear_gate_block("1")

        # Gate 2: enough new memories since last grooming
        last_grooming_at = await self._state.get("last_grooming_at", default=0.0)
        new_count = await self._count_new_memories(memory_db, last_grooming_at)
        if new_count < self._min_new_memories:
            self._report_gate_block(
                "2",
                f"only {new_count} new memories, need {self._min_new_memories}",
            )
            return False
        self._clear_gate_block("2")

        logging.info(
            f"[memory_grooming] All gates passed — {new_count} new memories since last run. "
            f"Starting grooming."
        )
        await self._state.set("last_grooming_at", time.time())
        return True

    async def action(self) -> None:
        """Schedule the grooming agent behind the shared AI lock (fire-and-forget).

        Queues behind any in-progress chat, dream, research, or scheduled task.
        The lock is the single source of truth — no cross-module checks needed.
        """
        memory_db = g_data.get("memory_db")
        if not memory_db:
            return

        async def _locked_groom() -> None:
            lock = g_data.get("ai_lock")
            if lock:
                if not await acquire_ai_lock(lock, label="memory_grooming"):
                    logging.error("[memory_grooming] Lock holder inactive — skipping grooming")
                    return
                try:
                    await self._run_locked_grooming()
                finally:
                    lock.release()
            else:
                await self._run_locked_grooming()  # fallback: lock not ready yet

        asyncio.create_task(_locked_groom(), name="memory_grooming_agent")

    async def _run_locked_grooming(self) -> None:
        """Inner grooming logic — called while holding the AI lock."""
        memory_db = g_data.get("memory_db")
        if not memory_db:
            return

        logging.info("[memory_grooming] Grooming agent starting...")
        start = time.time()

        try:
            report = await self._run_grooming_agent()
            elapsed = time.time() - start

            self._last_report = report
            self._log_structured_report(report, elapsed)
            await self._store_report_in_neo4j(memory_db, report)

        except Exception as e:
            logging.error(f"[memory_grooming] Grooming failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Internal — AI grooming agent
    # ------------------------------------------------------------------

    async def _run_grooming_agent(self) -> dict:
        """Spawn an AI agent with heartbeat-filtered tools to analyze the memory graph."""
        from lib.ai_providers import Message, ProviderRegistry
        from lib.services.tool_executor import execute_tool_loop
        from lib.services.tool_context import ToolContext

        cfg = g_data.get("cfg")
        if not cfg:
            raise RuntimeError("No config available for grooming agent")

        provider_config = cfg.data.get("providers", {}).get(self._provider_name, {})
        provider = ProviderRegistry.get_provider(self._provider_name, provider_config)

        ctx = await ToolContext.for_heartbeat("memory_grooming")

        auto_merge_note = (
            "AUTO-MERGE IS ENABLED. You may call dream_merge_memories."
            if self._auto_merge
            else "AUTO-MERGE IS DISABLED. Do NOT call dream_merge_memories or dream_delete_memory. Only FLAG issues."
        )

        messages = [
            Message(role="system", content=_GROOMING_SYSTEM_PROMPT),
            Message(
                role="user",
                content=f"Begin memory grooming. {auto_merge_note} "
                f"Focus on memories created or changed recently. "
                f"Start with Phase 1 (dream_get_stats + dream_list_memories).",
            ),
        ]

        response = await provider.chat(messages, ctx.schemas, model=self._model_name)

        if response.tool_calls:
            response, _tool_msgs = await execute_tool_loop(
                provider=provider,
                messages=messages,
                tools=ctx.tools,
                model=self._model_name,
                initial_response=response,
                max_calls=self._max_tool_calls,
                max_rounds=self._max_tool_rounds,
            )

        return self._parse_report(response.content or "")

    # ------------------------------------------------------------------
    # Internal — report parsing
    # ------------------------------------------------------------------

    def _parse_report(self, text: str) -> dict:
        """Parse the AI's markdown report into structured sections."""
        sections = {
            "contradictions": [],
            "near_duplicates": [],
            "stale_flagged": [],
            "knowledge_gaps": [],
            "summary": "",
        }

        # Extract sections using the exact headers from the system prompt
        patterns = {
            "contradictions": r"## CONTRADICTIONS\s*\n(.*?)(?=\n## [A-Z_]+|\Z)",
            "near_duplicates": r"## NEAR_DUPLICATES\s*\n(.*?)(?=\n## [A-Z_]+|\Z)",
            "stale_flagged": r"## STALE_FLAGGED\s*\n(.*?)(?=\n## [A-Z_]+|\Z)",
            "knowledge_gaps": r"## KNOWLEDGE_GAPS\s*\n(.*?)(?=\n## [A-Z_]+|\Z)",
            "summary": r"## SUMMARY\s*\n(.*?)(?=\n## [A-Z_]+|\Z)",
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                raw = match.group(1).strip()
                if key == "summary":
                    sections[key] = raw
                else:
                    # Split into individual items (lines starting with -, *, or numbered)
                    items = []
                    for line in raw.split("\n"):
                        stripped = line.strip()
                        if stripped and not re.search(r"none found", stripped, re.IGNORECASE) and (
                            stripped.startswith("-")
                            or stripped.startswith("*")
                            or re.match(r"^\d+[\.\)]", stripped)
                        ):
                            items.append(re.sub(r"^[-*\d]+[\.\)]\s*", "", stripped).strip())
                    sections[key] = items

        return sections

    def _log_structured_report(self, report: dict, elapsed: float) -> None:
        """Log the grooming report in structured format."""
        total = sum(len(report.get(k, [])) for k in ("contradictions", "near_duplicates", "stale_flagged", "knowledge_gaps"))

        logging.info(
            f"[memory_grooming] Grooming complete in {elapsed:.1f}s. "
            f"Issues found: contradictions={len(report.get('contradictions', []))}, "
            f"near_duplicates={len(report.get('near_duplicates', []))}, "
            f"stale={len(report.get('stale_flagged', []))}, "
            f"knowledge_gaps={len(report.get('knowledge_gaps', []))}"
        )

        if total > 0:
            logging.info(f"[memory_grooming] Summary: {report.get('summary', 'N/A')}")
            for category in ("contradictions", "near_duplicates", "stale_flagged", "knowledge_gaps"):
                items = report.get(category, [])
                for item in items[:5]:  # Log max 5 per category
                    logging.info(f"[memory_grooming] [{category}] {item}")

    async def _store_report_in_neo4j(self, memory_db, report: dict) -> None:
        """Persist the grooming report to Neo4j for later reference."""
        try:
            driver = memory_db.get_driver()
            async with driver.session() as session:
                await session.run(
                    """MERGE (r:GroomingReport {id: 'latest'})
                    SET r.contradictions = $contradictions,
                        r.near_duplicates = $near_duplicates,
                        r.stale_flagged = $stale_flagged,
                        r.knowledge_gaps = $knowledge_gaps,
                        r.summary = $summary,
                        r.checked_at = $now
                    RETURN r""",
                    {
                        "contradictions": json.dumps(report.get("contradictions", [])),
                        "near_duplicates": json.dumps(report.get("near_duplicates", [])),
                        "stale_flagged": json.dumps(report.get("stale_flagged", [])),
                        "knowledge_gaps": json.dumps(report.get("knowledge_gaps", [])),
                        "summary": report.get("summary", ""),
                        "now": int(time.time() * 1000),
                    },
                )
        except Exception as e:
            logging.debug(f"[memory_grooming] Failed to persist report to Neo4j: {e}")

    # ------------------------------------------------------------------
    # Internal — SQLite state (tracks last grooming timestamp)
    # ------------------------------------------------------------------

    async def _init_db(self) -> None:
        """Create the grooming_state table if it doesn't exist."""
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS grooming_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )"""
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Internal — memory counting
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
            logging.warning(f"[memory_grooming] Failed to count new memories: {e}")
            return 0

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def last_report(self) -> dict:
        """Return the last grooming report."""
        return self._last_report
