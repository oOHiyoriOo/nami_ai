"""
heartbeat_module.py — Pluggable module interface for HeartbeatService.

Each module implements condition() to decide if it should run, and action()
to perform the work. Modules are sorted by priority (higher runs first) and
rate-limited by cooldown_seconds.
"""

import logging
import time
from abc import ABC, abstractmethod

import aiosqlite


class HeartbeatModule(ABC):
    """Abstract base for a HeartbeatService pluggable check module."""

    name: str = "unnamed"
    priority: int = 0
    cooldown_seconds: float = 60.0
    enabled: bool = True

    def __init__(self) -> None:
        self._last_run_at: float = 0.0
        self._db_path: str | None = None
        self._conn: aiosqlite.Connection | None = None
        # Gate-block tracking for rate-limited INFO/WARNING logging.
        # Keys are gate IDs (e.g. "1.5", "3").  Updated by
        # _report_gate_block() and _clear_gate_block().
        self._gate_blocked_since: dict[str, float] = {}
        self._gate_last_logged: dict[str, float] = {}

    def _report_gate_block(
        self,
        gate: str,
        message: str,
        log_interval: float = 1800.0,
    ) -> None:
        """
        Emit a rate-limited log when a gate is persistently blocking.

        - First blockage: logs at INFO.
        - Every ``log_interval`` seconds thereafter: logs at WARNING, including
          how long the gate has been stuck.

        This keeps logs quiet for expected/transient blocks (e.g. "not daytime
        yet") while making prolonged or unexpected blocks clearly visible without
        requiring DEBUG log level.

        Args:
            gate:         Short gate identifier, e.g. "2" or "1.5".
            message:      Human-readable reason for the block.
            log_interval: Seconds between repeat WARNING emissions (default 30 min).
        """
        now = time.time()
        if gate not in self._gate_blocked_since:
            self._gate_blocked_since[gate] = now
            self._gate_last_logged[gate] = now
            logging.info(f"[heartbeat.{self.name}] Gate {gate} blocking: {message}")
            return

        last_logged = self._gate_last_logged.get(gate, 0.0)
        if now - last_logged >= log_interval:
            self._gate_last_logged[gate] = now
            blocked_secs = now - self._gate_blocked_since[gate]
            h = int(blocked_secs / 3600)
            m = int((blocked_secs % 3600) / 60)
            logging.warning(
                f"[heartbeat.{self.name}] Gate {gate} still blocking after "
                f"{h}h{m:02d}m: {message}"
            )

    def _clear_gate_block(self, gate: str) -> None:
        """Mark a gate as no longer blocking (call when the gate passes)."""
        self._gate_blocked_since.pop(gate, None)
        self._gate_last_logged.pop(gate, None)

    async def _ensure_conn(self) -> aiosqlite.Connection:
        """Return the persistent connection, opening it lazily if needed."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
        return self._conn

    @abstractmethod
    async def condition(self) -> bool:
        """Return True if this module should run right now."""
        ...

    @abstractmethod
    async def action(self) -> None:
        """Perform the module's work (AI call, health check, grooming, etc.)."""
        ...

    async def run(self) -> bool:
        """
        Check cooldown + condition and execute action if both pass.

        Returns True if the action was executed.
        """
        if not self.enabled:
            return False

        now = time.time()
        if self.cooldown_seconds > 0 and now - self._last_run_at < self.cooldown_seconds:
            return False

        try:
            if not await self.condition():
                return False
        except Exception as e:
            logging.error(
                f"[heartbeat.{self.name}] condition() raised: {e}", exc_info=True
            )
            return False

        try:
            logging.info(f"[heartbeat.{self.name}] Running action (priority={self.priority})")
            await self.action()
            self._last_run_at = time.time()
            return True
        except Exception as e:
            logging.error(
                f"[heartbeat.{self.name}] action() raised: {e}", exc_info=True
            )
            return False

    def seconds_since_last_run(self) -> float:
        """Return seconds since this module last ran (0 if never)."""
        if self._last_run_at == 0:
            return 0.0
        return time.time() - self._last_run_at

    def __lt__(self, other: "HeartbeatModule") -> bool:
        # Higher priority sorts first
        return self.priority > other.priority

    async def start(self) -> None:
        """Called when the heartbeat service starts. Override for resource setup."""

    async def stop(self) -> None:
        """Called when the heartbeat service stops. Override for resource cleanup."""
