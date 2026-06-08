"""
Tests for lib/services/heartbeat_module.py — HeartbeatModule

Covers:
- run() returns False when module is disabled
- run() returns False when still on cooldown
- run() returns True when enabled, cooldown expired, condition passes, action succeeds
- run() updates _last_run_at after successful action, not on failure
- run() handles condition() exceptions (returns False, logs error, skips action)
- run() handles action() exceptions (returns False, logs error, does NOT update _last_run_at)
- seconds_since_last_run() returns 0.0 when never run, correct elapsed after run
- __lt__() sorts higher priority before lower priority
"""

import asyncio
import importlib.util
import logging
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

_PROJECT = Path(__file__).parent.parent

_hbm_spec = importlib.util.spec_from_file_location(
    "heartbeat_module", _PROJECT / "lib" / "services" / "heartbeat_module.py",
)
_hbm = importlib.util.module_from_spec(_hbm_spec)
_hbm_spec.loader.exec_module(_hbm)
HeartbeatModule = _hbm.HeartbeatModule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _ConcreteModule(HeartbeatModule):
    """Concrete HeartbeatModule for testing run() logic directly."""

    def __init__(self, name="test_mod", priority=50, cooldown=60):
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


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def test_run_returns_false_when_disabled():
    """run() returns False when module is disabled (self.enabled = False)."""
    m = _ConcreteModule()
    m.enabled = False

    result = asyncio.run(m.run())

    assert result is False
    m._act.assert_not_awaited()


def test_run_returns_false_when_on_cooldown():
    """run() returns False when still within cooldown window."""
    m = _ConcreteModule(cooldown=9999)

    # First run succeeds
    result1 = asyncio.run(m.run())
    assert result1 is True
    assert m._act.await_count == 1

    # Second run should be blocked by cooldown
    result2 = asyncio.run(m.run())
    assert result2 is False
    assert m._act.await_count == 1  # no additional action calls


def test_run_returns_false_when_condition_false():
    """run() returns False when condition() returns False."""
    m = _ConcreteModule()
    m._cond.return_value = False

    result = asyncio.run(m.run())

    assert result is False
    m._act.assert_not_awaited()


def test_run_returns_true_when_all_pass():
    """run() returns True when enabled, cooldown expired, condition passes, action succeeds."""
    m = _ConcreteModule(cooldown=0)

    result = asyncio.run(m.run())

    assert result is True
    m._act.assert_awaited_once()


def test_run_updates_last_run_at_after_success():
    """run() updates _last_run_at after a successful action."""
    m = _ConcreteModule(cooldown=0)
    before = time.time()

    asyncio.run(m.run())

    assert m._last_run_at >= before
    assert m._last_run_at > 0


def test_run_does_not_update_last_run_at_on_condition_fail():
    """run() does NOT update _last_run_at when condition() returns False."""
    m = _ConcreteModule(cooldown=0)
    m._cond.return_value = False

    asyncio.run(m.run())

    assert m._last_run_at == 0.0


def test_run_does_not_update_last_run_at_on_action_fail():
    """run() does NOT update _last_run_at when action() raises."""
    m = _ConcreteModule(cooldown=0)
    m._act.side_effect = RuntimeError("kaboom")

    asyncio.run(m.run())

    assert m._last_run_at == 0.0


def test_run_handles_condition_exception():
    """run() catches condition() exceptions, logs error, returns False, and does NOT call action()."""
    m = _ConcreteModule()
    m._cond.side_effect = RuntimeError("boom")

    with patch("logging.error") as mock_log_error:
        result = asyncio.run(m.run())

    assert result is False
    m._act.assert_not_awaited()
    mock_log_error.assert_called_once()
    log_msg = mock_log_error.call_args[0][0]
    assert "condition() raised" in log_msg
    assert m._last_run_at == 0.0


def test_run_handles_action_exception():
    """run() catches action() exceptions, logs error, returns False, and does NOT update _last_run_at."""
    m = _ConcreteModule(cooldown=0)
    m._act.side_effect = RuntimeError("kaboom")

    with patch("logging.error") as mock_log_error:
        result = asyncio.run(m.run())

    assert result is False
    m._act.assert_awaited_once()
    mock_log_error.assert_called_once()
    log_msg = mock_log_error.call_args[0][0]
    assert "action() raised" in log_msg
    assert m._last_run_at == 0.0


# ---------------------------------------------------------------------------
# seconds_since_last_run()
# ---------------------------------------------------------------------------

def test_seconds_since_last_run_returns_zero_when_never_run():
    """seconds_since_last_run() returns 0.0 when module has never run."""
    m = _ConcreteModule()

    assert m.seconds_since_last_run() == 0.0


def test_seconds_since_last_run_returns_elapsed_after_run():
    """seconds_since_last_run() returns correct elapsed seconds after a run."""
    m = _ConcreteModule(cooldown=0)
    before = time.time()

    asyncio.run(m.run())

    elapsed = m.seconds_since_last_run()
    now = time.time()
    max_elapsed = now - before

    assert elapsed >= 0
    assert elapsed <= max_elapsed


def test_seconds_since_last_run_monotonic():
    """seconds_since_last_run() increases as time passes."""
    m = _ConcreteModule(cooldown=0)

    asyncio.run(m.run())
    first = m.seconds_since_last_run()
    time.sleep(0.1)
    second = m.seconds_since_last_run()

    assert second > first


# ---------------------------------------------------------------------------
# __lt__()
# ---------------------------------------------------------------------------

def test_lt_sorts_higher_priority_before_lower():
    """__lt__() sorts higher priority before lower priority."""
    high = _ConcreteModule("high", priority=100)
    low = _ConcreteModule("low", priority=10)

    assert high < low       # high priority sorts before (is "less than") low
    assert not (low < high)


def test_lt_equal_priority():
    """__lt__() is False when priorities are equal (stable sort order)."""
    a = _ConcreteModule("a", priority=50)
    b = _ConcreteModule("b", priority=50)

    assert not (a < b)
    assert not (b < a)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
