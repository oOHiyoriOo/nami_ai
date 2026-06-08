"""
heartbeat_service.py — Generic autonomous tick loop with pluggable modules.

HeartbeatService is the foundation of Nami's autonomous behavior. It runs a
loop on a configurable interval, checking preconditions for registered modules
and dispatching their actions when conditions are met.

Architecture:
    HeartbeatService
    ├── tick loop (every N seconds)
    ├── module registry (priority-sorted pluggable check modules)
    ├── cooldown / condition evaluation per module
    ├── action dispatch (deterministic or AI pipeline call)
    ├── watchdog (alert if no activity for too long)
    └── state persistence (scheduler.db)

Modules implement the HeartbeatModule interface (heartbeat_module.py).
"""

import asyncio
import logging
import time
from typing import Any

import aiosqlite

from lib.services.heartbeat_module import HeartbeatModule


class HeartbeatService:
    """
    Generic autonomous tick loop.

    On each tick, iterates registered modules in priority order, checks
    their condition() and cooldowns, and executes action() when ready.

    The watchdog tracks the last time any event was processed. If no
    event is seen within watchdog_threshold seconds, a warning is logged.
    """

    def __init__(
        self,
        config: Any,
        db_path: str = "scheduler.db",
    ) -> None:
        """
        Args:
            config: Full application config dict (from config.yml).
            db_path: Path to SQLite DB for heartbeat state.
        """
        self.config = config
        self.db_path = db_path

        hb_cfg = config.data.get("heartbeat", {})
        self._enabled: bool = hb_cfg.get("enabled", True)
        self._tick_interval: float = hb_cfg.get("tick_interval", 30)
        self._watchdog_threshold: float = hb_cfg.get("watchdog_threshold", 300)
        self._modules_cfg: dict[str, dict] = hb_cfg.get("modules", {})

        self._modules: list[HeartbeatModule] = []
        self._task: asyncio.Task | None = None
        self._last_event_at: float = time.time()
        self._tick_count: int = 0

        logging.info(
            f"[heartbeat] HeartbeatService initialised — "
            f"tick_interval={self._tick_interval}s, "
            f"watchdog_threshold={self._watchdog_threshold}s"
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background tick loop."""
        await self._init_db()
        # Start all registered modules
        for module in self._modules:
            try:
                await module.start()
                logging.debug(f"[heartbeat] Module {module.name} started")
            except Exception as e:
                logging.error(f"[heartbeat] Failed to start module {module.name}: {e}", exc_info=True)
        self._task = asyncio.create_task(self._tick_loop(), name="heartbeat_tick")
        logging.info("[heartbeat] HeartbeatService started")

    async def stop(self) -> None:
        """Stop the tick loop and all modules gracefully."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Stop all registered modules
        for module in self._modules:
            try:
                await module.stop()
                logging.debug(f"[heartbeat] Module {module.name} stopped")
            except Exception as e:
                logging.error(f"[heartbeat] Failed to stop module {module.name}: {e}", exc_info=True)
        # Close own DB connection
        if hasattr(self, '_conn') and self._conn:
            await self._conn.close()
        logging.info("[heartbeat] HeartbeatService stopped")

    def record_event(self) -> None:
        """
        Call this whenever any event is processed (message, task, api call).

        Resets the watchdog timer so the heartbeat knows the system is alive.
        """
        self._last_event_at = time.time()

    # ------------------------------------------------------------------
    # EventBus subscribers
    # ------------------------------------------------------------------

    async def _on_startup_complete(self, event) -> None:
        """Record startup event to reset watchdog and log."""
        self.record_event()
        logging.info("[heartbeat] Received system.startup_complete — watchdog reset")

    async def _on_memory_extracted(self, event) -> None:
        """Record memory extraction event to keep watchdog alive."""
        self.record_event()
        stored = event.data.get("stored", 0)
        if stored > 0:
            logging.debug(
                f"[heartbeat] memory.extracted: {stored} memories stored "
                f"for {event.data.get('user_name', 'unknown')} — watchdog reset"
            )

    # ------------------------------------------------------------------
    # Module registry
    # ------------------------------------------------------------------

    def register(self, module: HeartbeatModule) -> None:
        """Register a pluggable check module."""
        self._modules.append(module)
        self._modules.sort()  # highest priority first
        mod_cfg = self._modules_cfg.get(module.name, {})
        module.enabled = mod_cfg.get("enabled", True)
        if "cooldown" in mod_cfg:
            module.cooldown_seconds = mod_cfg["cooldown"]
        logging.info(
            f"[heartbeat] Module registered: {module.name} "
            f"(priority={module.priority}, cooldown={module.cooldown_seconds}s, "
            f"enabled={module.enabled})"
        )

    def get_module(self, name: str) -> HeartbeatModule | None:
        """Retrieve a registered module by name."""
        for m in self._modules:
            if m.name == name:
                return m
        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _init_db(self) -> None:
        """Create the heartbeat_state table if it doesn't exist."""
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS heartbeat_state (
                module TEXT PRIMARY KEY,
                last_run REAL,
                last_result TEXT,
                run_count INTEGER DEFAULT 0
            )
        """)
        await self._conn.commit()

    async def _ensure_conn(self) -> aiosqlite.Connection:
        """Return the persistent connection, opening it lazily if needed."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(self.db_path)
        return self._conn

    async def _save_run(self, module_name: str, success: bool) -> None:
        """Persist module run result to DB."""
        try:
            db = await self._ensure_conn()
            await db.execute(
                """INSERT OR REPLACE INTO heartbeat_state
                   (module, last_run, last_result, run_count)
                   VALUES (?, ?, ?,
                     COALESCE((SELECT run_count FROM heartbeat_state WHERE module=?), 0) + 1)""",
                (module_name, time.time(), "ok" if success else "error", module_name),
            )
            await self._conn.commit()
        except Exception as e:
            logging.warning(f"[heartbeat] Failed to persist run state: {e}")

    async def _tick_loop(self) -> None:
        """Main loop: sleep interval, then tick all modules."""
        while True:
            try:
                await asyncio.sleep(self._tick_interval)
                self._tick_count += 1
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[heartbeat] Tick loop error: {e}", exc_info=True)

    async def _tick(self) -> None:
        """One tick: check watchdog, then evaluate all modules."""
        self._check_watchdog()

        if not self._modules:
            return

        module_count = len(self._modules)
        ran_count = 0
        for module in self._modules:
            result = await module.run()
            if result:
                ran_count += 1
                await self._save_run(module.name, True)

        if ran_count > 0 and self._tick_count % 10 == 0:
            logging.info(
                f"[heartbeat] Tick #{self._tick_count}: {ran_count}/{module_count} modules ran"
            )

    def _check_watchdog(self) -> None:
        """Log when no event has been seen within the watchdog window.

        Uses DEBUG level — an idle system (no chat activity) is perfectly normal
        and not actionable. The watchdog is only useful for detecting stalls in
        high-traffic deployments; logging at WARNING causes noise in quiet ones.
        """
        idle = time.time() - self._last_event_at
        if idle > self._watchdog_threshold:
            logging.debug(
                f"[heartbeat] watchdog: idle for {idle:.0f}s "
                f"(threshold={self._watchdog_threshold}s)"
            )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return a snapshot of heartbeat state for monitoring."""
        return {
            "enabled": self._enabled,
            "tick_interval": self._tick_interval,
            "watchdog_threshold": self._watchdog_threshold,
            "tick_count": self._tick_count,
            "last_event_seconds_ago": time.time() - self._last_event_at,
            "modules": [
                {
                    "name": m.name,
                    "enabled": m.enabled,
                    "priority": m.priority,
                    "cooldown_seconds": m.cooldown_seconds,
                    "seconds_since_last_run": m.seconds_since_last_run(),
                }
                for m in self._modules
            ],
        }
