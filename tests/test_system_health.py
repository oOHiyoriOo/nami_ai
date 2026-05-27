"""
Tests for lib/services/heartbeat_modules/system_health.py

Covers:
- HealthState enum and CheckState dataclass
- State transitions: healthy → degraded → unhealthy → healthy
- Per-check state tracking (independent for each check)
- Timing measurements in _run_check
- _check_neo4j with timing and graceful error handling
- _check_providers per-provider reporting
- _check_adapters graceful when not configured
- _check_sandbox graceful when not configured, timeout handling
- _check_memory_stats degradation detection
- report() structure and overall status aggregation
- _nami_message lookup for all transition types
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.services.heartbeat_modules.system_health import (
    HealthState,
    CheckState,
    SystemHealthCheck,
    _nami_message,
)

# ---------------------------------------------------------------------------
# HealthState enum
# ---------------------------------------------------------------------------

def test_health_state_values():
    assert HealthState.HEALTHY.value == "healthy"
    assert HealthState.DEGRADED.value == "degraded"
    assert HealthState.UNHEALTHY.value == "unhealthy"


# ---------------------------------------------------------------------------
# CheckState — per-check state tracking
# ---------------------------------------------------------------------------

def test_check_state_initial_healthy():
    cs = CheckState("test")
    assert cs.state == HealthState.HEALTHY
    assert cs.consecutive_failures == 0
    assert cs.last_ok is True


def test_check_state_ok_stays_healthy():
    cs = CheckState("test")
    result = cs.record(ok=True, latency_ms=5.0)
    assert result is None  # no state change
    assert cs.state == HealthState.HEALTHY
    assert cs.consecutive_failures == 0


def test_check_state_degraded_after_one_failure():
    cs = CheckState("test")
    result = cs.record(ok=False, latency_ms=100.0, error="boom")
    assert result == HealthState.DEGRADED
    assert cs.state == HealthState.DEGRADED
    assert cs.consecutive_failures == 1
    assert cs.last_error == "boom"


def test_check_state_stays_degraded_on_second_failure():
    cs = CheckState("test")
    cs.record(ok=False)
    result = cs.record(ok=False)
    assert result is None  # already degraded
    assert cs.state == HealthState.DEGRADED
    assert cs.consecutive_failures == 2


def test_check_state_unhealthy_after_three_failures():
    cs = CheckState("test")
    cs.record(ok=False)
    cs.record(ok=False)
    result = cs.record(ok=False)
    assert result == HealthState.UNHEALTHY
    assert cs.state == HealthState.UNHEALTHY
    assert cs.consecutive_failures == 3


def test_check_state_recovery_from_degraded():
    cs = CheckState("test")
    cs.record(ok=False)  # degraded
    result = cs.record(ok=True, latency_ms=3.0)
    assert result == HealthState.HEALTHY
    assert cs.state == HealthState.HEALTHY
    assert cs.consecutive_failures == 0


def test_check_state_recovery_from_unhealthy():
    cs = CheckState("test")
    cs.record(ok=False)
    cs.record(ok=False)
    cs.record(ok=False)  # unhealthy
    result = cs.record(ok=True, latency_ms=4.0)
    assert result == HealthState.HEALTHY
    assert cs.state == HealthState.HEALTHY
    assert cs.consecutive_failures == 0


def test_check_state_latency_recorded():
    cs = CheckState("test")
    cs.record(ok=True, latency_ms=42.5)
    assert cs.last_latency_ms == 42.5


def test_check_state_error_cleared_on_recovery():
    cs = CheckState("test")
    cs.record(ok=False, error="down")
    assert cs.last_error == "down"
    cs.record(ok=True)
    assert cs.last_error is None


# ---------------------------------------------------------------------------
# _nami_message — personal assistant tone
# ---------------------------------------------------------------------------

def test_nami_message_neo4j_degraded():
    msg = _nami_message("neo4j", "healthy", "degraded")
    assert msg is not None
    assert "Erinnerungen" in msg


def test_nami_message_neo4j_unhealthy():
    msg = _nami_message("neo4j", "healthy", "unhealthy")
    assert msg is not None
    assert "nicht erreichbar" in msg


def test_nami_message_neo4j_recovery():
    msg = _nami_message("neo4j", "unhealthy", "healthy")
    assert msg is not None
    assert "wieder da" in msg


def test_nami_message_providers_degraded():
    msg = _nami_message("providers", "healthy", "degraded")
    assert msg is not None
    assert "Denk-Engine" in msg


def test_nami_message_providers_unhealthy():
    msg = _nami_message("providers", "degraded", "unhealthy")
    assert msg is not None
    assert "gar nicht denken" in msg


def test_nami_message_unknown_transition():
    msg = _nami_message("nonexistent", "healthy", "degraded")
    assert msg is None


# ---------------------------------------------------------------------------
# SystemHealthCheck — module tests
# ---------------------------------------------------------------------------

def _make_check():
    """Create a SystemHealthCheck for testing."""
    return SystemHealthCheck()


def test_module_attributes():
    check = _make_check()
    assert check.name == "system_health"
    assert check.priority == 100
    assert check.cooldown_seconds == 300


def test_all_checks_registered():
    check = _make_check()
    assert set(check._checks.keys()) == {
        "neo4j", "providers", "adapters", "sandbox", "memory_stats"
    }
    for cs in check._checks.values():
        assert cs.state == HealthState.HEALTHY


def test_condition_always_true():
    check = _make_check()
    async def run():
        return await check.condition()
    result = asyncio.run(run())
    assert result is True


# ---------------------------------------------------------------------------
# _check_neo4j
# ---------------------------------------------------------------------------

def test_check_neo4j_no_memory_db():
    check = _make_check()
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = None
        ok, detail = asyncio.run(check._check_neo4j(5.0))
    assert ok is False
    assert "not in g_data" in detail


def test_check_neo4j_success():
    check = _make_check()
    mock_db = MagicMock()
    mock_record = MagicMock()
    mock_record.__getitem__.return_value = 1
    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=mock_record)
    mock_session = MagicMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_driver.session.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_db.get_driver.return_value = mock_driver

    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = mock_db
        ok, detail = asyncio.run(check._check_neo4j(5.0))
    assert ok is True
    assert detail == "ok"


def test_check_neo4j_timeout():
    check = _make_check()
    mock_db = MagicMock()
    mock_session = MagicMock()
    mock_session.run = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_driver.session.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_db.get_driver.return_value = mock_driver

    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = mock_db
        ok, detail = asyncio.run(check._check_neo4j(5.0))
    assert ok is False
    assert "timed out" in detail


# ---------------------------------------------------------------------------
# _check_providers
# ---------------------------------------------------------------------------

def test_check_providers_no_config():
    check = _make_check()
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = None
        ok, detail = asyncio.run(check._check_providers(10))
    assert ok is False
    assert "no config" in detail


def test_check_providers_none_configured():
    check = _make_check()
    mock_cfg = MagicMock()
    mock_cfg.data = {"providers": {}}
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = mock_cfg
        ok, detail = asyncio.run(check._check_providers(10))
    assert ok is True
    assert "no providers configured" in detail


# ---------------------------------------------------------------------------
# _check_adapters
# ---------------------------------------------------------------------------

def test_check_adapters_not_configured():
    check = _make_check()
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = None
        ok, detail = asyncio.run(check._check_adapters())
    assert ok is True
    assert "no adapter WS server configured" in detail


def test_check_adapters_not_running():
    check = _make_check()
    mock_ws = MagicMock()
    mock_ws.connected_adapters = []  # no adapters connected yet
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = mock_ws
        ok, detail = asyncio.run(check._check_adapters())
    assert ok is True
    assert "waiting" in detail or "no adapters" in detail


def test_check_adapters_ok():
    check = _make_check()
    mock_ws = MagicMock()
    mock_ws.connected_adapters = ["discord", "whatsapp"]
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = mock_ws
        ok, detail = asyncio.run(check._check_adapters())
    assert ok is True
    assert "discord" in detail


# ---------------------------------------------------------------------------
# _check_sandbox
# ---------------------------------------------------------------------------

def test_check_sandbox_not_configured():
    check = _make_check()
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = None
        ok, detail = asyncio.run(check._check_sandbox())
    assert ok is True
    assert "not configured" in detail


# ---------------------------------------------------------------------------
# _check_memory_stats
# ---------------------------------------------------------------------------

def test_check_memory_stats_not_available():
    check = _make_check()
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = None
        ok, detail = asyncio.run(check._check_memory_stats())
    assert ok is True
    assert "not available" in detail


def test_check_memory_stats_healthy():
    check = _make_check()
    mock_analytics = MagicMock()
    mock_analytics.diagnose_issues = AsyncMock(return_value={
        "health_score": 100,
        "severity": "low",
        "issues": [],
    })
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = mock_analytics
        ok, detail = asyncio.run(check._check_memory_stats())
    assert ok is True
    assert "100" in detail


def test_check_memory_stats_high_severity():
    check = _make_check()
    mock_analytics = MagicMock()
    mock_analytics.diagnose_issues = AsyncMock(return_value={
        "health_score": 40,
        "severity": "high",
        "issues": ["High memory count", "Most memories old", "Low access count"],
    })
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = mock_analytics
        ok, detail = asyncio.run(check._check_memory_stats())
    assert ok is False
    assert "40" in detail


def test_check_memory_stats_medium_severity():
    check = _make_check()
    mock_analytics = MagicMock()
    mock_analytics.diagnose_issues = AsyncMock(return_value={
        "health_score": 80,
        "severity": "medium",
        "issues": ["High unused memory ratio"],
    })
    with patch("lib.services.heartbeat_modules.system_health.g_data") as mock_gd:
        mock_gd.get.return_value = mock_analytics
        ok, detail = asyncio.run(check._check_memory_stats())
    assert ok is True  # medium severity still returns ok
    assert "80" in detail


# ---------------------------------------------------------------------------
# _run_check — state transition integration
# ---------------------------------------------------------------------------

def test_run_check_state_transition_to_degraded():
    check = _make_check()

    async def failing_check():
        return False, "test failure"

    asyncio.run(check._run_check("adapters", failing_check()))
    cs = check._checks["adapters"]
    assert cs.state == HealthState.DEGRADED
    assert cs.consecutive_failures == 1


def test_run_check_state_recovery():
    check = _make_check()

    async def failing_check():
        return False, "fail"
    async def ok_check():
        return True, "ok"

    asyncio.run(check._run_check("adapters", failing_check()))
    asyncio.run(check._run_check("adapters", ok_check()))
    cs = check._checks["adapters"]
    assert cs.state == HealthState.HEALTHY
    assert cs.consecutive_failures == 0


def test_run_check_state_all_healthy():
    """Full cycle: degraded → unhealthy → healthy."""
    check = _make_check()

    async def fail():
        return False, "fail"
    async def ok():
        return True, "ok"

    # 3 failures → unhealthy
    asyncio.run(check._run_check("neo4j", fail()))
    asyncio.run(check._run_check("neo4j", fail()))
    asyncio.run(check._run_check("neo4j", fail()))
    assert check._checks["neo4j"].state == HealthState.UNHEALTHY

    # 1 recovery → healthy
    asyncio.run(check._run_check("neo4j", ok()))
    assert check._checks["neo4j"].state == HealthState.HEALTHY


def test_run_check_records_timing():
    check = _make_check()

    async def slow_check():
        await asyncio.sleep(0.01)
        return True, "ok"

    asyncio.run(check._run_check("neo4j", slow_check()))
    cs = check._checks["neo4j"]
    assert cs.last_latency_ms > 0


def test_run_check_handles_exception():
    check = _make_check()

    async def crashing_check():
        raise RuntimeError("unexpected crash")

    asyncio.run(check._run_check("neo4j", crashing_check()))
    cs = check._checks["neo4j"]
    assert cs.state == HealthState.DEGRADED
    assert cs.consecutive_failures == 1
    assert cs.last_error == "unexpected crash"


# ---------------------------------------------------------------------------
# report()
# ---------------------------------------------------------------------------

def test_report_all_healthy():
    check = _make_check()
    report = check.report()
    assert report["overall"] == "healthy"
    assert len(report["checks"]) == 5
    for name, cr in report["checks"].items():
        assert cr["state"] == "healthy", f"{name} should be healthy"
        assert cr["consecutive_failures"] == 0


def test_report_degraded():
    check = _make_check()

    async def fail():
        return False, "fail"

    asyncio.run(check._run_check("neo4j", fail()))
    report = check.report()
    assert report["overall"] == "degraded"
    assert report["checks"]["neo4j"]["state"] == "degraded"


def test_report_unhealthy():
    check = _make_check()

    async def fail():
        return False, "fail"

    # Make neo4j unhealthy and providers degraded
    for _ in range(3):
        asyncio.run(check._run_check("neo4j", fail()))
    asyncio.run(check._run_check("providers", fail()))

    report = check.report()
    assert report["overall"] == "unhealthy"
    assert report["checks"]["neo4j"]["state"] == "unhealthy"
    assert report["checks"]["providers"]["state"] == "degraded"


def test_report_last_run_ago():
    check = _make_check()
    report = check.report()
    # Never run — should be 0.0
    assert report["last_run_ago"] == 0.0


# ---------------------------------------------------------------------------
# status property (backward compat)
# ---------------------------------------------------------------------------

def test_status_is_report():
    check = _make_check()
    s = check.status
    assert "overall" in s
    assert "checks" in s
    assert "last_run_ago" in s
