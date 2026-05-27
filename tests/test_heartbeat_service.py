"""
Tests for lib/services/heartbeat_service.py — HeartbeatService

Covers:
- register() — adds module, sorts by priority, applies config
- get_module() — returns module by name or None
- _tick() — executes module when condition passes
- _tick() — skips module when condition returns False
- _save_run() — persists run record to SQLite
- _check_watchdog() — fires when last event exceeds threshold
- record_event() — resets watchdog timer
- start() / stop() — proper lifecycle
"""

import asyncio
import importlib.util
import os
import sys
import tempfile
import time
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.configuration_file import ConfigurationFile

_PROJECT = Path(__file__).parent.parent

# Import modules directly (bypass services/__init__.py to avoid heavy deps)
_hbm_spec = importlib.util.spec_from_file_location(
    "heartbeat_module", _PROJECT / "lib" / "services" / "heartbeat_module.py",
)
_hbm = importlib.util.module_from_spec(_hbm_spec)
_hbm_spec.loader.exec_module(_hbm)
HeartbeatModule = _hbm.HeartbeatModule

_hbs_spec = importlib.util.spec_from_file_location(
    "heartbeat_service", _PROJECT / "lib" / "services" / "heartbeat_service.py",
)
_hbs = importlib.util.module_from_spec(_hbs_spec)
_hbs_spec.loader.exec_module(_hbs)
HeartbeatService = _hbs.HeartbeatService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockModule(HeartbeatModule):
    """Minimal heartbeat module for testing."""
    def __init__(self, name="mock", priority=50, cooldown=60):
        super().__init__()
        self.name = name
        self.priority = priority
        self.cooldown_seconds = cooldown
        self._cond = AsyncMock(return_value=True)
        self._act = AsyncMock()

    async def condition(self) -> bool:
        return await self._cond()

    async def action(self) -> None:
        await self._act()


def _make_config(heartbeat_overrides=None) -> ConfigurationFile:
    data = {
        "default_provider": "ollama",
        "default_model": "test-model",
        "providers": {"ollama": {"base_url": "http://localhost"}},
        "heartbeat": heartbeat_overrides or {},
    }
    return ConfigurationFile("fake.yml", data)


def _tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------

def test_register_adds_module():
    """register() adds a module and sorts by priority."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)
    m1 = _MockModule("low", priority=10)
    m2 = _MockModule("high", priority=100)

    svc.register(m1)
    svc.register(m2)

    assert len(svc._modules) == 2
    assert svc._modules[0].name == "high"  # higher priority first
    assert svc._modules[1].name == "low"


def test_register_applies_module_config():
    """register() reads enabled and cooldown from heartbeat config."""
    cfg = _make_config({
        "modules": {
            "custom": {"enabled": False, "cooldown": 123},
        }
    })
    svc = HeartbeatService(cfg)
    m = _MockModule("custom")

    svc.register(m)

    assert m.enabled is False
    assert m.cooldown_seconds == 123


def test_get_module_returns_by_name():
    """get_module() finds registered module by name."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)
    m = _MockModule("finder")
    svc.register(m)

    assert svc.get_module("finder") is m


def test_get_module_returns_none_for_unknown():
    """get_module() returns None for unregistered name."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)

    assert svc.get_module("nope") is None


# ---------------------------------------------------------------------------
# _tick()
# ---------------------------------------------------------------------------

def test_tick_executes_module_when_condition_passes():
    """_tick() runs module.action() when condition() returns True."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)
    m = _MockModule("runner")
    m._cond.return_value = True
    svc.register(m)

    asyncio.run(svc._tick())

    m._act.assert_awaited_once()


def test_tick_skips_module_when_condition_false():
    """_tick() does NOT run action() when condition() returns False."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)
    m = _MockModule("skipper")
    m._cond.return_value = False
    svc.register(m)

    asyncio.run(svc._tick())

    m._act.assert_not_awaited()


def test_tick_skips_disabled_module():
    """_tick() skips modules where enabled=False."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)
    m = _MockModule("off")
    svc.register(m)          # register() applies config, may set enabled=True
    m.enabled = False        # override AFTER register

    asyncio.run(svc._tick())

    m._act.assert_not_awaited()


def test_tick_skips_module_on_cooldown():
    """_tick() skips module still within cooldown window."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)
    m = _MockModule("cooldown", cooldown=9999)
    m._cond.return_value = True
    svc.register(m)

    # First run: should execute
    asyncio.run(svc._tick())
    assert m._act.await_count == 1

    # Second run: still on cooldown, should skip
    asyncio.run(svc._tick())
    assert m._act.await_count == 1  # no additional calls


def test_tick_handles_condition_exception():
    """_tick() catches condition() exceptions and skips the module."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)
    m = _MockModule("crashy")
    m._cond.side_effect = RuntimeError("boom")
    svc.register(m)

    # Should not raise
    asyncio.run(svc._tick())
    m._act.assert_not_awaited()


def test_tick_handles_action_exception():
    """_tick() catches action() exceptions and does not bubble up."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)
    m = _MockModule("crashy-action")
    m._cond.return_value = True
    m._act.side_effect = RuntimeError("kaboom")
    svc.register(m)

    asyncio.run(svc._tick())  # should not raise
    m._act.assert_awaited_once()


def test_tick_no_modules_is_noop():
    """_tick() does nothing when no modules registered."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)

    asyncio.run(svc._tick())  # should complete without error


# ---------------------------------------------------------------------------
# _save_run()
# ---------------------------------------------------------------------------

def test_save_run_persists_to_sqlite():
    """_save_run() writes module run record to SQLite."""
    import aiosqlite
    db = _tmp_db()
    cfg = _make_config()
    svc = HeartbeatService(cfg, db_path=db)

    async def setup_and_save():
        await svc._init_db()
        await svc._save_run("test_mod", True)
    asyncio.run(setup_and_save())

    async def verify():
        async with aiosqlite.connect(db) as conn:
            async with conn.execute("SELECT * FROM heartbeat_state WHERE module='test_mod'") as cur:
                row = await cur.fetchone()
                assert row is not None
                assert row[2] == "ok"
                assert row[3] == 1
    asyncio.run(verify())
    os.unlink(db)


def test_save_run_increments_run_count():
    """_save_run() increments run_count for subsequent runs."""
    import aiosqlite
    db = _tmp_db()
    cfg = _make_config()
    svc = HeartbeatService(cfg, db_path=db)

    async def setup_and_save():
        await svc._init_db()
        await svc._save_run("counter", True)
        await svc._save_run("counter", True)
        await svc._save_run("counter", False)
    asyncio.run(setup_and_save())

    async def verify():
        async with aiosqlite.connect(db) as conn:
            async with conn.execute("SELECT last_result, run_count FROM heartbeat_state WHERE module='counter'") as cur:
                row = await cur.fetchone()
                assert row[0] == "error"  # last result
                assert row[1] == 3  # total runs
    asyncio.run(verify())
    os.unlink(db)


def test_save_run_handles_db_error():
    """_save_run() catches DB errors without raising."""
    db = _tmp_db()
    cfg = _make_config()
    svc = HeartbeatService(cfg, db_path=db)

    # Corrupt the db
    os.unlink(db)
    with open(db, "w") as f:
        f.write("garbage")

    asyncio.run(svc._save_run("fail", True))  # should not raise
    os.unlink(db)


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------

def test_check_watchdog_warns_when_idle():
    """_check_watchdog() logs debug message when idle exceeds threshold."""
    cfg = _make_config({"watchdog_threshold": 1})
    svc = HeartbeatService(cfg)

    # Move last_event_at far into the past
    svc._last_event_at = time.time() - 9999

    with patch("logging.debug") as mock_debug:
        svc._check_watchdog()
        assert mock_debug.called
        called_text = mock_debug.call_args[0][0]
        assert "watchdog" in called_text.lower()


def test_check_watchdog_silent_when_events_recent():
    """_check_watchdog() is silent when events are within threshold."""
    cfg = _make_config({"watchdog_threshold": 9999})
    svc = HeartbeatService(cfg)
    svc._last_event_at = time.time()  # just now

    with patch("logging.warning") as mock_warn:
        svc._check_watchdog()
        mock_warn.assert_not_called()


def test_record_event_resets_watchdog():
    """record_event() resets the watchdog timer."""
    cfg = _make_config({"watchdog_threshold": 1})
    svc = HeartbeatService(cfg)

    # Simulate idle state
    svc._last_event_at = time.time() - 9999
    idle_before = time.time() - svc._last_event_at
    assert idle_before > svc._watchdog_threshold

    # Record an event — should reset
    svc.record_event()

    with patch("logging.warning") as mock_warn:
        svc._check_watchdog()
        mock_warn.assert_not_called()


# ---------------------------------------------------------------------------
# Lifecycle — start / stop
# ---------------------------------------------------------------------------

def test_start_initializes_db_and_creates_task():
    """start() creates DB table and asyncio task."""
    db = _tmp_db()
    cfg = _make_config()
    svc = HeartbeatService(cfg, db_path=db)

    async def run():
        await svc.start()
        assert svc._task is not None
        assert svc._task.get_name() == "heartbeat_tick"

        # Let one tick run, then stop
        await asyncio.sleep(0.05)
        await svc.stop()
        assert svc._task.done()

    asyncio.run(run())

    # Verify DB table was created
    import aiosqlite

    async def verify():
        async with aiosqlite.connect(db) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='heartbeat_state'"
            ) as cur:
                row = await cur.fetchone()
                assert row is not None
    asyncio.run(verify())
    os.unlink(db)


def test_stop_cancels_task_gracefully():
    """stop() cancels the tick loop task without error."""
    db = _tmp_db()
    cfg = _make_config()
    svc = HeartbeatService(cfg, db_path=db)

    async def run():
        await svc.start()
        assert svc._task is not None
        await svc.stop()
        assert svc._task.done()
        assert svc._task.cancelled()

    asyncio.run(run())
    os.unlink(db)


def test_stop_when_not_started_is_safe():
    """stop() is a no-op when start() was never called."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)

    async def run():
        await svc.stop()  # should not raise

    asyncio.run(run())


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

def test_status_returns_snapshot():
    """status() returns a dict with all heartbeat state keys."""
    cfg = _make_config({"tick_interval": 15, "watchdog_threshold": 60})
    svc = HeartbeatService(cfg)
    m = _MockModule("status_mod", priority=75, cooldown=90)
    svc.register(m)

    s = svc.status()

    assert s["enabled"] is True
    assert s["tick_interval"] == 15
    assert s["watchdog_threshold"] == 60
    assert s["tick_count"] == 0
    assert "last_event_seconds_ago" in s
    assert len(s["modules"]) == 1
    assert s["modules"][0]["name"] == "status_mod"
    assert s["modules"][0]["priority"] == 75
    assert s["modules"][0]["cooldown_seconds"] == 90


# ---------------------------------------------------------------------------
# EventBus subscribers
# ---------------------------------------------------------------------------

def test_on_startup_complete_resets_watchdog():
    """_on_startup_complete() calls record_event() and logs."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)
    svc._last_event_at = time.time() - 9999

    event = MagicMock()
    asyncio.run(svc._on_startup_complete(event))

    assert (time.time() - svc._last_event_at) < 1


def test_on_memory_extracted_resets_watchdog():
    """_on_memory_extracted() calls record_event()."""
    cfg = _make_config()
    svc = HeartbeatService(cfg)
    svc._last_event_at = time.time() - 9999

    event = MagicMock()
    event.data = {"stored": 5, "user_name": "test"}
    asyncio.run(svc._on_memory_extracted(event))

    assert (time.time() - svc._last_event_at) < 1


# ---------------------------------------------------------------------------
# _tick_loop()
# ---------------------------------------------------------------------------

def test_tick_loop_increments_tick_count():
    """_tick_loop() increments _tick_count and calls _tick()."""
    cfg = _make_config({"tick_interval": 0.01, "watchdog_threshold": 9999})
    svc = HeartbeatService(cfg)
    m = _MockModule("increment")
    m._cond.return_value = True
    svc.register(m)

    async def run():
        loop_task = asyncio.create_task(svc._tick_loop())
        await asyncio.sleep(0.05)  # let a few ticks fire
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

        assert svc._tick_count >= 2
        assert m._act.await_count >= 1
    asyncio.run(run())


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def test_init_with_empty_heartbeat_config():
    """HeartbeatService initialises with defaults when heartbeat config is empty."""
    cfg = _make_config()  # empty heartbeat
    svc = HeartbeatService(cfg)
    assert svc._enabled is True
    assert svc._tick_interval == 30
    assert svc._watchdog_threshold == 300
    assert svc._tick_count == 0
    assert svc._modules == []
    assert svc._task is None


def test_init_with_full_heartbeat_config():
    """HeartbeatService reads all values from config."""
    cfg = _make_config({
        "enabled": False,
        "tick_interval": 10,
        "watchdog_threshold": 120,
    })
    svc = HeartbeatService(cfg)
    assert svc._enabled is False
    assert svc._tick_interval == 10
    assert svc._watchdog_threshold == 120


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
