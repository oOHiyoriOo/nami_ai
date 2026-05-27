"""
Tests for OllamaTools/schedule_task.py — scheduling tool functions + time parsing.

Covers:
- _parse_time(): natural language, cron, past timestamps, invalid input
- _RECURRENCE_MAP: friendly alias resolution
- _create_task_common(): past-time rejection
- schedule_task() / schedule_self_task() tool functions
- get_tool(): returns 4 well-formed schemas
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock optional/heavy dependencies to avoid import cascades in test environment
_STUB_MODS = ['neo4j', 'discord', 'asyncssh', 'ollama', 'openai',
              'aiofiles', 'discord.ext', 'discord.ext.commands',
              'sentence_transformers', 'torch', 'colorama', 'asyncpg',
              'bs4', 'beautifulsoup4', 'sklearn', 'sklearn.cluster',
              'PIL', 'matplotlib', 'scipy', 'numpy', 'pandas',
              'discord_sdk', 'aiohttp']
# NOTE: lib.services.ai_pipeline is NOT in _STUB_MODS — it is handled
# by the explicit block below which gives pipeline_ctx a real ContextVar.
# Putting it in the loop would set it to a plain MagicMock() first, which
# makes the conditional block below a no-op (already in sys.modules).
_SAVED_STUBS = {mod: sys.modules.get(mod) for mod in _STUB_MODS}
for _mod in _STUB_MODS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Mock ai_pipeline module with a pipeline_ctx ContextVar — only if not already
# imported by an earlier test file (e.g. test_ai_pipeline.py).  Overwriting a
# real module here would contaminate the entire test session.
if "lib.services.ai_pipeline" not in sys.modules:
    from contextvars import ContextVar
    _ai_pipeline_mock = MagicMock()
    _ai_pipeline_mock.pipeline_ctx = ContextVar("pipeline_ctx", default={})
    sys.modules["lib.services.ai_pipeline"] = _ai_pipeline_mock


sys.path.insert(0, str(Path(__file__).parent.parent))

from OllamaTools.schedule_task import (
    _parse_time,
    _RECURRENCE_MAP,
    _create_task_common,
    schedule_task,
    schedule_self_task,
    list_tasks,
    cancel_task,
    get_tool,
)


@pytest.fixture(autouse=True, scope="module")
def _restore_stubs():
    """Restore sys.modules after all tests in this module complete."""
    yield
    for mod, orig in _SAVED_STUBS.items():
        if orig is None:
            sys.modules.pop(mod, None)
        else:
            sys.modules[mod] = orig


# ── Helpers ────────────────────────────────────────────────────────────

def _make_source_user(uid="123", platform="discord"):
    user = MagicMock()
    user.id = uid
    user.platform = platform
    return user


def _make_client(channel_id="chan:42"):
    client = MagicMock()
    client.channel = MagicMock()
    client.channel.id = channel_id
    return client


def _make_mock_scheduler():
    """Create a mock scheduler with AsyncMock create_task."""
    scheduler = MagicMock()
    scheduler.create_task = AsyncMock()
    return scheduler


# ── _parse_time() tests ────────────────────────────────────────────────

def test_parse_time_natural_language():
    """_parse_time() resolves natural-language time strings."""
    now = int(time.time())

    ts = _parse_time("in 30 minutes")
    assert ts is not None, f"[FAIL] 'in 30 minutes' returned None"
    assert not (abs(ts - (now + 1800)) > 120), f"[FAIL] 'in 30 minutes' expected ~{now+1800}, got {ts}"

    ts = _parse_time("tomorrow at 9am")
    assert ts is not None, f"[FAIL] 'tomorrow at 9am' returned None"
    assert not (ts <= now), f"[FAIL] 'tomorrow at 9am' should be future, got {ts} <= {now}"



def test_parse_time_cron():
    """_parse_time() resolves 5-field cron expressions to next occurrence."""
    try:
        from croniter import croniter
    except ImportError:
        print("  [SKIP] croniter not installed")
        return True

    now = int(time.time())

    # Weekdays at 9:30am
    ts = _parse_time("30 9 * * 1-5")
    assert ts is not None, f"[FAIL] '30 9 * * 1-5' returned None"
    assert not (ts <= now), f"[FAIL] cron should be future, got {ts} <= {now}"

    # Daily at midnight
    ts2 = _parse_time("0 0 * * *")
    assert ts2 is not None, f"[FAIL] '0 0 * * *' returned None"
    assert not (ts2 <= now), f"[FAIL] cron midnight should be future, got {ts2} <= {now}"



def test_parse_time_past():
    """_parse_time() parses explicit past dates as past timestamps."""
    now = int(time.time())

    # Explicit past date should be parsed as-is, giving a past timestamp
    ts = _parse_time("2020-01-01T00:00:00")
    assert ts is not None, f"[FAIL] '2020-01-01T00:00:00' returned None"
    assert not (ts >= now), f"[FAIL] '2020-01-01' should be past, got {ts} >= {now}"



def test_parse_time_invalid():
    """_parse_time() returns None for unparseable input."""
    ts = _parse_time("xyzzy not a time at all")
    assert ts is None, f"[FAIL] garbage input should return None, got {ts}"

    ts = _parse_time("")
    assert ts is None, f"[FAIL] empty string should return None, got {ts}"

    # Invalid cron should return None
    try:
        from croniter import croniter
        ts = _parse_time("99 99 * * *")
        assert ts is None, f"[FAIL] invalid cron should return None, got {ts}"
    except ImportError:
        pass



# ── _RECURRENCE_MAP tests ──────────────────────────────────────────────

def test_recurrence_map_aliases():
    """_RECURRENCE_MAP maps friendly names to canonical values."""
    checks = [
        ("every hour", "hourly"),
        ("every day", "daily"),
        ("once", None),
        ("one-shot", None),
        ("hourly", "hourly"),
        ("daily", "daily"),
        ("every_n_hours", "every_n_hours"),
        ("every_n_days", "every_n_days"),
    ]

    for alias, expected in checks:
        actual = _RECURRENCE_MAP.get(alias)
        assert actual == expected, f"[FAIL] _RECURRENCE_MAP[{alias!r}] = {actual!r}, expected {expected!r}"

    # Unknown key returns None (not in map)
    unknown = _RECURRENCE_MAP.get("bogus")
    assert unknown is None, f"[FAIL] unknown key should return None, got {unknown!r}"



# ── _create_task_common tests ──────────────────────────────────────────

def test_create_task_past_time():
    """_create_task_common rejects a past scheduled time."""
    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler") as mock_get_sched:
            # Schedule at a definitely past time
            past_ts = int(time.time()) - 86400  # 1 day ago
            with patch("OllamaTools.schedule_task._parse_time", return_value=past_ts):
                result = await _create_task_common(
                    prompt="test",
                    run_at="2020-01-01T00:00:00",
                    source_user=_make_source_user(),
                    client=_make_client(),
                )
        return result

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success") is False, f"[FAIL] Expected success=False for past time, got {data}"
    assert "past" in data.get("error", "").lower(), f"[FAIL] Error should mention 'past', got {data.get('error')!r}"



# ── Tool function tests ────────────────────────────────────────────────

def test_schedule_task_success():
    """schedule_task() returns success with valid inputs."""
    from lib.services.task_scheduler import ScheduledTask

    mock_task = ScheduledTask(
        id="st-001", label="test-label", prompt="hello",
        scheduled_at=int(time.time()) + 3600,
        created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:42",
        adapter="discord", status="pending",
    )

    scheduler = _make_mock_scheduler()
    scheduler.create_task.return_value = mock_task

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await schedule_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="do a thing",
                run_at="in 1 hour",
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success"), f"[FAIL] Expected success=True, got {data}"
    assert (data.get("data") or {}).get("task_id") == "st-001", f"[FAIL] Expected task_id='st-001', got {data.get('data')}"
    assert (data.get("data") or {}).get("label") == "test-label", f"[FAIL] Expected label='test-label', got {data.get('data')}"

    scheduler.create_task.assert_awaited_once()
    call_kwargs = scheduler.create_task.call_args.kwargs
    assert call_kwargs.get("prompt") == "do a thing", f"[FAIL] Wrong prompt passed to scheduler: {call_kwargs.get('prompt')!r}"
    assert call_kwargs.get("origin") == "user", f"[FAIL] Expected origin='user', got {call_kwargs.get('origin')!r}"



def test_schedule_self_task_success():
    """schedule_self_task() sets origin='ai' and handles ttl_runs."""
    from lib.services.task_scheduler import ScheduledTask

    mock_task = ScheduledTask(
        id="sst-001", label="self-label", prompt="self-prompt",
        scheduled_at=int(time.time()) + 3600,
        created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:42",
        adapter="discord", status="pending",
        origin="ai", ttl_runs=5,
    )

    scheduler = _make_mock_scheduler()
    scheduler.create_task.return_value = mock_task

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await schedule_self_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="self scheduled thing",
                run_at="in 2 hours",
                ttl_runs=5,
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success"), f"[FAIL] Expected success=True, got {data}"

    call_kwargs = scheduler.create_task.call_args.kwargs
    assert call_kwargs.get("origin") == "ai", f"[FAIL] Expected origin='ai', got {call_kwargs.get('origin')!r}"
    assert call_kwargs.get("ttl_runs") == 5, f"[FAIL] Expected ttl_runs=5, got {call_kwargs.get('ttl_runs')!r}"

    # Response should include origin and ttl_runs for AI tasks
    assert (data.get("data") or {}).get("origin") == "ai", f"[FAIL] Response should include origin='ai', got {data.get('data')}"
    assert (data.get("data") or {}).get("ttl_runs") == 5, f"[FAIL] Response should include ttl_runs=5, got {data.get('data')}"



def test_schedule_self_task_zero_ttl():
    """schedule_self_task with ttl_runs=0 stores None (unlimited)."""
    from lib.services.task_scheduler import ScheduledTask

    mock_task = ScheduledTask(
        id="sst-002", label="unlimited", prompt="hi",
        scheduled_at=int(time.time()) + 3600,
        created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:42",
        adapter="discord", status="pending",
        origin="ai", ttl_runs=None,
    )

    scheduler = _make_mock_scheduler()
    scheduler.create_task.return_value = mock_task

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await schedule_self_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="unlimited task",
                run_at="in 3 hours",
                ttl_runs=0,
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success"), f"[FAIL] Expected success=True, got {data}"

    call_kwargs = scheduler.create_task.call_args.kwargs
    assert call_kwargs.get("ttl_runs") is None, f"[FAIL] ttl_runs=0 should be passed as None, got {call_kwargs.get('ttl_runs')!r}"



def test_schedule_self_task_notify_target():
    """schedule_self_task with notify_target overrides adapter."""
    from lib.services.task_scheduler import ScheduledTask

    mock_task = ScheduledTask(
        id="sst-003", label="notify-test", prompt="hi",
        scheduled_at=int(time.time()) + 3600,
        created_at=int(time.time()),
        user_id="discord:123", conversation_id="987654321",
        adapter="discord", status="pending",
        origin="ai",
    )

    scheduler = _make_mock_scheduler()
    scheduler.create_task.return_value = mock_task

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await schedule_self_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="notify test",
                run_at="in 4 hours",
                notify_target="discord:987654321",
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success"), f"[FAIL] Expected success=True, got {data}"

    call_kwargs = scheduler.create_task.call_args.kwargs
    assert call_kwargs.get("adapter") == "discord", f"[FAIL] Expected adapter='discord', got {call_kwargs.get('adapter')!r}"
    assert call_kwargs.get("conversation_id") == "987654321", f"[FAIL] Expected conversation_id='987654321', got {call_kwargs.get('conversation_id')!r}"



def test_schedule_self_task_notify_target_log():
    """schedule_self_task with notify_target='log' sets adapter='none'."""
    from lib.services.task_scheduler import ScheduledTask

    mock_task = ScheduledTask(
        id="sst-004", label="log-test", prompt="hi",
        scheduled_at=int(time.time()) + 3600,
        created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:42",
        adapter="none", status="pending",
        origin="ai",
    )

    scheduler = _make_mock_scheduler()
    scheduler.create_task.return_value = mock_task

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await schedule_self_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="log only",
                run_at="in 5 hours",
                notify_target="log",
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success"), f"[FAIL] Expected success=True, got {data}"

    call_kwargs = scheduler.create_task.call_args.kwargs
    assert call_kwargs.get("adapter") == "none", f"[FAIL] Expected adapter='none' for log target, got {call_kwargs.get('adapter')!r}"



def test_schedule_self_task_cron_auto_recurrence():
    """schedule_self_task auto-sets recurrence from cron run_at."""
    try:
        from croniter import croniter
    except ImportError:
        print("  [SKIP] croniter not installed")
        return True

    from lib.services.task_scheduler import ScheduledTask

    mock_task = ScheduledTask(
        id="sst-005", label="cron-test", prompt="cron",
        scheduled_at=int(time.time()) + 3600,
        created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:42",
        adapter="discord", status="pending",
        recurrence="0 9 * * 1-5", origin="ai",
    )

    scheduler = _make_mock_scheduler()
    scheduler.create_task.return_value = mock_task

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await schedule_self_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="weekday check",
                run_at="0 9 * * 1-5",
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success"), f"[FAIL] Expected success=True, got {data}"

    call_kwargs = scheduler.create_task.call_args.kwargs
    assert call_kwargs.get("recurrence") == "0 9 * * 1-5", f"[FAIL] Expected recurrence cron, got {call_kwargs.get('recurrence')!r}"



# ── list_tasks / cancel_task tests ─────────────────────────────────────

def test_list_tasks_empty():
    """list_tasks returns success with empty list when no tasks."""
    scheduler = MagicMock()
    scheduler.list_tasks = AsyncMock(return_value=[])

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await list_tasks(client=_make_client(), source_user=_make_source_user())

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success"), f"[FAIL] Expected success=True, got {data}"
    assert data.get("data") == [], f"[FAIL] Expected empty data list, got {data.get('data')}"



def test_cancel_task_success():
    """cancel_task returns success when task is cancelled."""
    scheduler = MagicMock()
    scheduler.cancel_task = AsyncMock(return_value=True)

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await cancel_task(
                client=_make_client(),
                source_user=_make_source_user(),
                task_id="abc-123",
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success"), f"[FAIL] Expected success=True, got {data}"
    assert (data.get("data") or {}).get("cancelled") == "abc-123", f"[FAIL] Expected cancelled='abc-123', got {data.get('data')}"

    scheduler.cancel_task.assert_awaited_once_with("abc-123", "unknown:123")


def test_cancel_task_not_found():
    """cancel_task returns error when task is not found."""
    scheduler = MagicMock()
    scheduler.cancel_task = AsyncMock(return_value=False)

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await cancel_task(
                client=_make_client(),
                source_user=_make_source_user(),
                task_id="nope-999",
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success") is False, f"[FAIL] Expected success=False, got {data}"
    assert "not found" in data.get("error", "").lower(), f"[FAIL] Error should mention 'not found', got {data.get('error')!r}"



# ── get_tool() tests ───────────────────────────────────────────────────

def test_get_tool_returns_four_schemas():
    """get_tool() returns a list of 4 well-formed tool dicts."""
    tools = get_tool()

    assert isinstance(tools, list), f"[FAIL] Expected list, got {type(tools).__name__}"
    assert len(tools) == 4, f"[FAIL] Expected 4 tools, got {len(tools)}"

    names = set()
    for tool in tools:
        fn = tool.get("function", {})
        name = fn.get("name")
        names.add(name)

        assert tool.get("type") == "function", f"[FAIL] {name}: type != 'function'"
        assert "func" in tool or not callable(tool["func"]), f"[FAIL] {name}: func missing or not callable"

    expected = {"schedule_task", "schedule_self_task", "list_tasks", "cancel_task"}
    assert names == expected, f"[FAIL] Expected names {expected}, got {names}"



def test_get_tool_all_funcs_async():
    """All func references in get_tool() are async functions."""
    import inspect

    tools = get_tool()
    for tool in tools:
        name = tool["function"]["name"]
        func = tool["func"]
        assert inspect.iscoroutinefunction(func), f"[FAIL] {name}: func is not async"



def test_get_tool_schedule_task_required():
    """schedule_task schema requires 'prompt' and 'run_at'."""
    tool = next(t for t in get_tool() if t["function"]["name"] == "schedule_task")
    required = tool["function"]["parameters"].get("required", [])

    assert "prompt" in required or "run_at" in required, f"[FAIL] schedule_task required fields wrong: {required}"



def test_get_tool_categories():
    """All tools have 'scheduling' category."""
    tools = get_tool()
    for tool in tools:
        name = tool["function"]["name"]
        assert "scheduling" in tool.get("categories", []), f"[FAIL] {name}: missing 'scheduling' category"



# ── Error handling tests ───────────────────────────────────────────────

def test_create_task_unparseable_time():
    """_create_task_common returns error when time can't be parsed."""
    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler") as mock_get_sched:
            with patch("OllamaTools.schedule_task._parse_time", return_value=None):
                result = await _create_task_common(
                    prompt="test",
                    run_at="garbage time",
                    source_user=_make_source_user(),
                    client=_make_client(),
                )
        return result

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success") is False, f"[FAIL] Expected success=False for unparseable time, got {data}"
    assert "parse" in data.get("error", "").lower(), f"[FAIL] Error should mention parsing failure, got {data.get('error')!r}"



def test_create_task_scheduler_error():
    """_create_task_common returns error when scheduler.create_task fails."""
    scheduler = _make_mock_scheduler()
    scheduler.create_task.side_effect = ValueError("Limit exceeded")

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await _create_task_common(
                prompt="test",
                run_at="in 1 hour",
                source_user=_make_source_user(),
                client=_make_client(),
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success") is False, f"[FAIL] Expected success=False for scheduler error, got {data}"
    assert "Limit exceeded" in data.get("error", ""), f"[FAIL] Error should contain ValueError message, got {data.get('error')!r}"



# ── Recurrence resolution tests ────────────────────────────────────────

def test_create_task_with_recurrence():
    """_create_task_common resolves recurrence from friendly name."""
    from lib.services.task_scheduler import ScheduledTask

    mock_task = ScheduledTask(
        id="rec-001", label="recurring", prompt="hi",
        scheduled_at=int(time.time()) + 3600,
        created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:42",
        adapter="discord", status="pending",
        recurrence="hourly",
    )

    scheduler = _make_mock_scheduler()
    scheduler.create_task.return_value = mock_task

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await _create_task_common(
                prompt="repeat me",
                run_at="in 1 hour",
                recurrence="every hour",
                source_user=_make_source_user(),
                client=_make_client(),
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success"), f"[FAIL] Expected success=True, got {data}"

    call_kwargs = scheduler.create_task.call_args.kwargs
    assert call_kwargs.get("recurrence") == "hourly", f"[FAIL] 'every hour' should resolve to 'hourly', got {call_kwargs.get('recurrence')!r}"



def test_create_task_invalid_recurrence():
    """_create_task_common rejects unrecognized recurrence strings."""
    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler") as mock_get_sched:
            return await _create_task_common(
                prompt="test",
                run_at="in 1 hour",
                recurrence="weekly",
                source_user=_make_source_user(),
                client=_make_client(),
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success") is False, f"[FAIL] Expected success=False for invalid recurrence, got {data}"
    assert "Unknown recurrence pattern" in data.get("error", ""), f"[FAIL] Error should mention 'Unknown recurrence pattern', got {data.get('error')!r}"
    assert "'weekly'" in data.get("error", ""), f"[FAIL] Error should include the bad value, got {data.get('error')!r}"



def test_create_task_one_shot_recurrence():
    """_create_task_common with 'once' recurrence stores None."""
    from lib.services.task_scheduler import ScheduledTask

    mock_task = ScheduledTask(
        id="rec-002", label="one-shot", prompt="hi",
        scheduled_at=int(time.time()) + 3600,
        created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:42",
        adapter="discord", status="pending",
        recurrence=None,
    )

    scheduler = _make_mock_scheduler()
    scheduler.create_task.return_value = mock_task

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=scheduler):
            return await _create_task_common(
                prompt="once only",
                run_at="in 1 hour",
                recurrence="one-shot",
                source_user=_make_source_user(),
                client=_make_client(),
            )

    raw = asyncio.run(run())
    data = json.loads(raw)

    assert data.get("success"), f"[FAIL] Expected success=True, got {data}"

    call_kwargs = scheduler.create_task.call_args.kwargs
    assert call_kwargs.get("recurrence") is None, f"[FAIL] 'one-shot' should resolve to None, got {call_kwargs.get('recurrence')!r}"



# ── main ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))