"""
Tests for lib/services/notification_pipeline.py

Covers:
- NotificationResult dataclass defaults
- notify() routes via send_conversation when adapter + conversation_id configured
- notify() falls back to log when no adapter/conversation_id configured
- notify() falls back to log when adapter_manager unavailable
- notify() returns error result on send failure
- Truncation for messages exceeding DEFAULT_MAX_CHARS
- _on_task_completed routing via send_conversation
- _on_task_completed fallback when adapter_manager missing, conv_id missing, or send fails
- _on_task_completed skips empty result and adapter='none'
- _on_task_missed calls notify() when adapter != 'none'
"""

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_spec = importlib.util.spec_from_file_location(
    "notification_pipeline",
    Path(__file__).parent.parent / "lib" / "services" / "notification_pipeline.py",
)
_notif_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_notif_mod)

NotificationPipeline = _notif_mod.NotificationPipeline
NotificationResult = _notif_mod.NotificationResult
DEFAULT_MAX_CHARS = _notif_mod.DEFAULT_MAX_CHARS


@contextmanager
def _mock_gdata(data):
    """Patch g_data singleton used inside notification_pipeline methods."""
    orig = _notif_mod.g_data
    _notif_mod.g_data = data
    try:
        yield
    finally:
        _notif_mod.g_data = orig


class _FakeConfig:
    def __init__(self, data):
        self.data = data


# ---------------------------------------------------------------------------
# NotificationResult
# ---------------------------------------------------------------------------

def test_result_defaults():
    r = NotificationResult(delivered=False, channel="log")
    assert r.delivered is False
    assert r.channel == "log"
    assert r.error is None
    assert r.elapsed_ms == 0.0
    assert r.truncated is False


# ---------------------------------------------------------------------------
# Routing: no adapter/conversation_id → log-only
# ---------------------------------------------------------------------------

def test_notify_log_only_no_config():
    cfg = _FakeConfig({"notifications": {}})
    pipeline = NotificationPipeline(cfg)
    result = asyncio_run(pipeline.notify("hello", source="test"))
    assert result.delivered is True
    assert result.channel == "log"
    assert result.truncated is False


def test_notify_log_only_missing_conversation_id():
    cfg = _FakeConfig({"notifications": {"adapter": "discord"}})
    pipeline = NotificationPipeline(cfg)
    result = asyncio_run(pipeline.notify("hello", source="test"))
    assert result.delivered is True
    assert result.channel == "log"


# ---------------------------------------------------------------------------
# Routing: adapter + conversation_id → send_conversation
# ---------------------------------------------------------------------------

def test_notify_delivers_via_send_conversation():
    cfg = _FakeConfig({
        "notifications": {"adapter": "discord", "conversation_id": "123456789"}
    })
    mock_am = MagicMock()
    mock_am.send_conversation = AsyncMock()

    with _mock_gdata({"adapter_manager": mock_am}):
        pipeline = NotificationPipeline(cfg)
        result = asyncio_run(pipeline.notify("test message", source="test"))

    assert result.delivered is True
    assert result.channel == "discord"
    mock_am.send_conversation.assert_awaited_once_with("discord", "123456789", "test message")


def test_notify_no_adapter_manager_falls_back_to_log():
    cfg = _FakeConfig({
        "notifications": {"adapter": "discord", "conversation_id": "123456789"}
    })
    with _mock_gdata({}):
        pipeline = NotificationPipeline(cfg)
        result = asyncio_run(pipeline.notify("test", source="test"))
    assert result.delivered is True
    assert result.channel == "log"


def test_notify_send_failure_returns_error_result():
    cfg = _FakeConfig({
        "notifications": {"adapter": "discord", "conversation_id": "123456789"}
    })
    mock_am = MagicMock()
    mock_am.send_conversation = AsyncMock(side_effect=RuntimeError("network down"))

    with _mock_gdata({"adapter_manager": mock_am}):
        pipeline = NotificationPipeline(cfg)
        result = asyncio_run(pipeline.notify("test", source="test"))

    assert result.delivered is False
    assert result.channel == "discord"
    assert "network down" in (result.error or "")


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

def test_truncation_applied_when_enabled():
    cfg = _FakeConfig({
        "notifications": {
            "adapter": "discord",
            "conversation_id": "123456789",
            "truncate": True,
        }
    })
    mock_am = MagicMock()
    mock_am.send_conversation = AsyncMock()
    long_msg = "x" * (DEFAULT_MAX_CHARS + 500)

    with _mock_gdata({"adapter_manager": mock_am}):
        pipeline = NotificationPipeline(cfg)
        result = asyncio_run(pipeline.notify(long_msg, source="test"))

    assert result.truncated is True
    assert result.original_length == DEFAULT_MAX_CHARS + 500
    assert result.final_length == DEFAULT_MAX_CHARS
    sent = mock_am.send_conversation.call_args[0][2]
    assert sent.endswith("...")
    assert len(sent) == DEFAULT_MAX_CHARS


def test_no_truncation_when_disabled():
    cfg = _FakeConfig({
        "notifications": {
            "adapter": "discord",
            "conversation_id": "12345",
            "truncate": False,
        }
    })
    mock_am = MagicMock()
    mock_am.send_conversation = AsyncMock()
    long_msg = "x" * (DEFAULT_MAX_CHARS + 500)

    with _mock_gdata({"adapter_manager": mock_am}):
        pipeline = NotificationPipeline(cfg)
        result = asyncio_run(pipeline.notify(long_msg, source="test"))

    assert result.truncated is False
    assert result.original_length == result.final_length
    assert len(mock_am.send_conversation.call_args[0][2]) == DEFAULT_MAX_CHARS + 500


def test_short_message_not_truncated():
    cfg = _FakeConfig({
        "notifications": {
            "adapter": "discord",
            "conversation_id": "12345",
            "truncate": True,
        }
    })
    mock_am = MagicMock()
    mock_am.send_conversation = AsyncMock()

    with _mock_gdata({"adapter_manager": mock_am}):
        pipeline = NotificationPipeline(cfg)
        result = asyncio_run(pipeline.notify("hello world", source="test"))

    assert result.truncated is False
    assert result.original_length == result.final_length


# ---------------------------------------------------------------------------
# _on_task_completed — conversation routing
# ---------------------------------------------------------------------------


class _FakeEvent:
    """Minimal stand-in for Event."""

    def __init__(self, type, data):
        self.type = type
        self.data = data


@pytest.mark.asyncio(loop_scope="function")
async def test_on_task_completed_routes_via_send_conversation():
    """When adapter and conversation_id are set, route via send_conversation."""
    cfg = _FakeConfig({"notifications": {}})

    mock_am = MagicMock()
    mock_am.send_conversation = AsyncMock()

    with _mock_gdata({"adapter_manager": mock_am}):
        pipeline = NotificationPipeline(cfg, event_bus=None)
        event = _FakeEvent(
            type="task.completed",
            data={
                "result": "Task done!",
                "adapter": "discord",
                "conversation_id": "123456789",
            },
        )
        await pipeline._on_task_completed(event)

    mock_am.send_conversation.assert_awaited_once_with("discord", "123456789", "Task done!")


@pytest.mark.asyncio(loop_scope="function")
async def test_on_task_completed_falls_back_when_no_adapter_manager():
    """When adapter_manager is missing, fall back to global notify."""
    cfg = _FakeConfig({"notifications": {}})

    with _mock_gdata({}):
        pipeline = NotificationPipeline(cfg, event_bus=None)
        pipeline.notify = AsyncMock()

        event = _FakeEvent(
            type="task.completed",
            data={
                "result": "Task done!",
                "adapter": "discord",
                "conversation_id": "123456789",
            },
        )
        await pipeline._on_task_completed(event)

    pipeline.notify.assert_awaited_once_with("Task done!", source="task:discord")


@pytest.mark.asyncio(loop_scope="function")
async def test_on_task_completed_falls_back_when_no_conversation_id():
    """When conversation_id is empty, fall back to global notify."""
    cfg = _FakeConfig({"notifications": {}})

    mock_am = MagicMock()
    with _mock_gdata({"adapter_manager": mock_am}):
        pipeline = NotificationPipeline(cfg, event_bus=None)
        pipeline.notify = AsyncMock()

        event = _FakeEvent(
            type="task.completed",
            data={
                "result": "Task done!",
                "adapter": "discord",
                "conversation_id": "",
            },
        )
        await pipeline._on_task_completed(event)

    pipeline.notify.assert_awaited_once_with("Task done!", source="task:discord")
    mock_am.send_conversation.assert_not_called()


@pytest.mark.asyncio(loop_scope="function")
async def test_on_task_completed_falls_back_when_routing_fails():
    """When send_conversation raises, fall back to global notify."""
    cfg = _FakeConfig({"notifications": {}})

    mock_am = MagicMock()
    mock_am.send_conversation = AsyncMock(side_effect=RuntimeError("Discord down"))

    with _mock_gdata({"adapter_manager": mock_am}):
        pipeline = NotificationPipeline(cfg, event_bus=None)
        pipeline.notify = AsyncMock()

        event = _FakeEvent(
            type="task.completed",
            data={
                "result": "Task done!",
                "adapter": "discord",
                "conversation_id": "123456789",
            },
        )
        await pipeline._on_task_completed(event)

    mock_am.send_conversation.assert_awaited_once()
    pipeline.notify.assert_awaited_once_with("Task done!", source="task:discord")


@pytest.mark.asyncio(loop_scope="function")
async def test_on_task_completed_skips_empty_result():
    """When result is empty, do nothing."""
    cfg = _FakeConfig({"notifications": {}})

    pipeline = NotificationPipeline(cfg, event_bus=None)
    pipeline.notify = AsyncMock()

    event = _FakeEvent(
        type="task.completed",
        data={
            "result": "",
            "adapter": "discord",
            "conversation_id": "123456789",
        },
    )
    await pipeline._on_task_completed(event)

    pipeline.notify.assert_not_called()


@pytest.mark.asyncio(loop_scope="function")
async def test_on_task_completed_skips_none_adapter():
    """When adapter='none', do nothing."""
    cfg = _FakeConfig({"notifications": {}})

    pipeline = NotificationPipeline(cfg, event_bus=None)
    pipeline.notify = AsyncMock()

    event = _FakeEvent(
        type="task.completed",
        data={
            "result": "Task done!",
            "adapter": "none",
            "conversation_id": "123456789",
        },
    )
    await pipeline._on_task_completed(event)

    pipeline.notify.assert_not_called()


# ---------------------------------------------------------------------------
# _on_task_missed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="function")
async def test_on_task_missed_calls_notify():
    """_on_task_missed delivers missed-task notification via notify()."""
    cfg = _FakeConfig({"notifications": {}})
    pipeline = NotificationPipeline(cfg, event_bus=None)
    pipeline.notify = AsyncMock()

    event = _FakeEvent(
        type="task.missed",
        data={"notification": "You missed this!", "adapter": "discord"},
    )
    await pipeline._on_task_missed(event)

    pipeline.notify.assert_awaited_once_with("You missed this!", source="task:discord")


@pytest.mark.asyncio(loop_scope="function")
async def test_on_task_missed_skips_none_adapter():
    """_on_task_missed skips delivery when adapter='none'."""
    cfg = _FakeConfig({"notifications": {}})
    pipeline = NotificationPipeline(cfg, event_bus=None)
    pipeline.notify = AsyncMock()

    event = _FakeEvent(
        type="task.missed",
        data={"notification": "Missed!", "adapter": "none"},
    )
    await pipeline._on_task_missed(event)

    pipeline.notify.assert_not_called()


# ---------------------------------------------------------------------------
# Result metadata
# ---------------------------------------------------------------------------

def test_result_includes_elapsed_time():
    cfg = _FakeConfig({"notifications": {}})
    pipeline = NotificationPipeline(cfg)
    result = asyncio_run(pipeline.notify("test", source="test"))
    assert result.elapsed_ms >= 0
    assert result.elapsed_ms < 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def asyncio_run(coro):
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, coro).result()
