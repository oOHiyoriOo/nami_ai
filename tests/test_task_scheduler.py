"""
Tests for TaskScheduler and schedule_task tools.

Covers:
- DB schema creation and task CRUD
- Per-user task limit enforcement
- cancel_task (own task / wrong user)
- Done tasks excluded from list
- Label and recurrence stored correctly
- _next_run() computes correct timestamps for all recurrence patterns
- _next_run() uses last_fired_at as anchor when available
- _next_run() handles 5-field cron expressions via croniter
- _next_run_from() rebases recurring tasks from an arbitrary timestamp
- _parse_time() resolves natural-language and ISO strings
- _parse_time() handles 5-field cron expressions
- get_tool() returns three well-formed schemas
- All tool funcs are async callables
- Tool loader handles list return from get_tool()
- schedule_task tool — success path and past-time rejection
- schedule_task tool — cron expression in run_at auto-sets recurrence
- list_tasks / cancel_task tool functions (mocked scheduler)
- Missed task notification text
"""

import asyncio
import importlib.util
import inspect
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiosqlite
from lib.services.task_scheduler import (
    TaskScheduler,
    ScheduledTask,
    _next_run,
    _next_run_from,
    _build_missed_notification,
    _CREATE_TABLE,
    MAX_PENDING_PER_USER,
    MAX_SELF_TASKS,
)
from lib.services.event_bus import Event, EventBus
from OllamaTools.schedule_task import _parse_time, get_tool


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

import tempfile
import os


def _tmp_scheduler() -> tuple[TaskScheduler, str]:
    """Return a TaskScheduler backed by a fresh temp file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return TaskScheduler(db_path=path), path


def _init_db(scheduler: TaskScheduler) -> None:
    """Create the DB schema synchronously."""
    async def _run():
        async with aiosqlite.connect(scheduler._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.commit()
    asyncio.run(_run())


def _make_args(**kwargs) -> dict:
    defaults = dict(
        prompt="check the weather",
        scheduled_at=int(time.time()) + 3600,
        user_id="discord:100",
        conversation_id="chan:200",
        adapter="discord",
    )
    defaults.update(kwargs)
    return defaults


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


def _task_with(recurrence, interval=None, base=1_000_000, last_fired_at=None):
    return ScheduledTask(
        id="x", prompt="p", scheduled_at=base, created_at=base,
        user_id="u", conversation_id="c", adapter="none", status="done",
        recurrence=recurrence, recurrence_interval=interval,
        last_fired_at=last_fired_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Storage — CRUD
# ─────────────────────────────────────────────────────────────────────────────

def test_create_and_list():
    """create_task stores a task; list_tasks returns it."""
    print("Test: create_task stores task, list_tasks returns it")
    s, _ = _tmp_scheduler()
    _init_db(s)

    async def run():
        t = await s.create_task(**_make_args())
        assert t.id and t.status == "pending", f"unexpected: {t}"
        tasks = await s.list_tasks("discord:100")
        assert len(tasks) == 1 and tasks[0].id == t.id
    asyncio.run(run())
    print("  [PASS]")


def test_cancel_own_task():
    """cancel_task returns True for own pending task."""
    print("Test: cancel_task succeeds for task owner")
    s, _ = _tmp_scheduler()
    _init_db(s)

    async def run():
        t = await s.create_task(**_make_args())
        ok = await s.cancel_task(t.id, "discord:100")
        assert ok is True
        tasks = await s.list_tasks("discord:100")
        assert len(tasks) == 0
    asyncio.run(run())
    print("  [PASS]")


def test_cancel_wrong_user():
    """cancel_task returns False when user doesn't own the task."""
    print("Test: cancel_task fails for wrong user")
    s, _ = _tmp_scheduler()
    _init_db(s)

    async def run():
        t = await s.create_task(**_make_args())
        ok = await s.cancel_task(t.id, "discord:999")
        assert ok is False
    asyncio.run(run())
    print("  [PASS]")


def test_per_user_limit():
    """create_task raises ValueError when user hits MAX_PENDING_PER_USER."""
    print(f"Test: per-user limit ({MAX_PENDING_PER_USER} tasks)")
    s, _ = _tmp_scheduler()
    _init_db(s)

    async def run():
        for _ in range(MAX_PENDING_PER_USER):
            await s.create_task(**_make_args())
        try:
            await s.create_task(**_make_args())
            assert False, "should have raised ValueError"
        except ValueError as e:
            assert "pending tasks" in str(e)
    asyncio.run(run())
    print("  [PASS]")


def test_done_tasks_excluded():
    """list_tasks excludes done/failed tasks."""
    print("Test: list_tasks excludes done tasks")
    s, _ = _tmp_scheduler()
    _init_db(s)

    async def run():
        t = await s.create_task(**_make_args())
        await s._set_status(t.id, "done")
        tasks = await s.list_tasks("discord:100")
        assert len(tasks) == 0
    asyncio.run(run())
    print("  [PASS]")


def test_label_and_recurrence_stored():
    """label and recurrence are persisted correctly."""
    print("Test: label and recurrence stored correctly")
    s, _ = _tmp_scheduler()
    _init_db(s)

    async def run():
        await s.create_task(**_make_args(label="morning check", recurrence="daily"))
        tasks = await s.list_tasks("discord:100")
        assert tasks[0].label == "morning check"
        assert tasks[0].recurrence == "daily"
    asyncio.run(run())
    print("  [PASS]")


# ─────────────────────────────────────────────────────────────────────────────
# Recurrence — _next_run()
# ─────────────────────────────────────────────────────────────────────────────

def test_next_run_patterns():
    """_next_run() computes correct timestamps for all patterns."""
    print("Test: _next_run() — all recurrence patterns")
    failures = []
    base = 1_000_000

    cases = [
        ("hourly",       None, base + 3600),
        ("daily",        None, base + 86400),
        ("every_n_hours", 3,   base + 3 * 3600),
        ("every_n_days",  7,   base + 7 * 86400),
        ("weekly",       None, None),   # unknown → None
        (None,           None, None),   # no recurrence → None
    ]

    for rec, interval, expected in cases:
        t = _task_with(rec, interval, base)
        got = _next_run(t)
        if got != expected:
            failures.append(f"recurrence={rec!r} interval={interval}: expected {expected}, got {got}")

    if failures:
        print("  [FAIL]\n  " + "\n  ".join(failures))
        return False
    print("  [PASS]")
    return True


def test_next_run_uses_last_fired_at():
    """_next_run() uses last_fired_at as anchor, not scheduled_at."""
    print("Test: _next_run() uses last_fired_at as anchor")
    base = 1_000_000
    fired = 2_000_000  # much later than base

    t = _task_with("daily", base=base, last_fired_at=fired)
    got = _next_run(t)
    expected = fired + 86400

    if got != expected:
        print(f"  [FAIL] expected {expected}, got {got}")
        return False
    print("  [PASS]")
    return True


def test_next_run_cron():
    """_next_run() handles 5-field cron expressions via croniter."""
    print("Test: _next_run() — cron expression")
    try:
        from croniter import croniter
    except ImportError:
        print("  [SKIP] croniter not installed")
        return True

    # "every minute" — simplest cron to test with
    base = 1_000_000
    t = _task_with("* * * * *", base=base)
    got = _next_run(t)

    if got is None:
        print("  [FAIL] expected a timestamp, got None")
        return False
    if got <= base:
        print(f"  [FAIL] next run {got} should be after anchor {base}")
        return False
    if got > base + 120:
        print(f"  [FAIL] next run {got} is too far in the future from {base}")
        return False
    print("  [PASS]")
    return True


def test_next_run_from_rebase():
    """_next_run_from() computes next occurrence from an arbitrary timestamp."""
    print("Test: _next_run_from() — rebase recurring task")
    base = 1_000_000
    rebase_ts = 5_000_000  # simulated 'now' after offline period

    t = _task_with("hourly", base=base)
    got = _next_run_from(t, rebase_ts)
    expected = rebase_ts + 3600

    if got != expected:
        print(f"  [FAIL] expected {expected}, got {got}")
        return False
    print("  [PASS]")
    return True


def test_missed_notification_text():
    """_build_missed_notification() produces a non-empty notification string."""
    print("Test: _build_missed_notification() — produces correct text")
    tasks = [
        ScheduledTask(
            id="t1", prompt="send daily report", scheduled_at=1_000_000,
            created_at=999_000, user_id="discord:1", conversation_id="c",
            adapter="discord", status="failed", label="daily-report",
        ),
        ScheduledTask(
            id="t2", prompt="check the weather", scheduled_at=1_000_100,
            created_at=999_000, user_id="discord:1", conversation_id="c",
            adapter="discord", status="failed",
        ),
    ]
    text = _build_missed_notification(tasks)

    failures = []
    if "missed" not in text.lower():
        failures.append("notification should mention 'missed'")
    if "daily-report" not in text:
        failures.append("notification should include label")
    if "send daily report" not in text:
        failures.append("notification should include prompt")
    if "check the weather" not in text:
        failures.append("notification should include second prompt")

    if failures:
        print("  [FAIL]\n  " + "\n  ".join(failures))
        return False
    print("  [PASS]")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# _parse_time()
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_time():
    """_parse_time() handles ISO dates, natural language, and garbage."""
    print("Test: _parse_time() — ISO, natural language, garbage")
    failures = []

    ts = _parse_time("2030-01-01T00:00:00")
    if ts is None or ts <= int(time.time()):
        failures.append("ISO date: expected future timestamp")

    ts = _parse_time("in 2 hours")
    expected = int(time.time()) + 7200
    if ts is None or abs(ts - expected) > 120:
        failures.append(f"'in 2 hours': expected ~{expected}, got {ts}")

    ts = _parse_time("not a time at all xyzzy")
    if ts is not None:
        failures.append(f"garbage input: expected None, got {ts}")

    if failures:
        print("  [FAIL]\n  " + "\n  ".join(failures))
        return False
    print("  [PASS]")
    return True


def test_parse_time_cron():
    """_parse_time() resolves cron expressions to next occurrence."""
    print("Test: _parse_time() — 5-field cron expression")
    try:
        from croniter import croniter
    except ImportError:
        print("  [SKIP] croniter not installed")
        return True

    now = int(time.time())
    ts = _parse_time("* * * * *")  # every minute
    if ts is None:
        print("  [FAIL] expected a timestamp, got None")
        return False
    if ts <= now or ts > now + 120:
        print(f"  [FAIL] cron next-run {ts} should be within 2 minutes of {now}")
        return False

    # Invalid cron → None
    ts_bad = _parse_time("99 99 * * *")
    if ts_bad is not None:
        print(f"  [FAIL] invalid cron should return None, got {ts_bad}")
        return False

    print("  [PASS]")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Tool schemas — get_tool()
# ─────────────────────────────────────────────────────────────────────────────

def test_get_tool_returns_four_schemas():
    """get_tool() returns a list of 4 well-formed tool dicts."""
    print("Test: get_tool() returns 4 valid schemas")
    failures = []
    tools = get_tool()

    if not isinstance(tools, list) or len(tools) != 4:
        failures.append(f"expected list of 4, got {type(tools).__name__}[{len(tools) if isinstance(tools, list) else '?'}]")
    else:
        names = set()
        for tool in tools:
            for key in ("type", "function", "func"):
                if key not in tool:
                    failures.append(f"missing key '{key}' in tool")
            fn = tool.get("function", {})
            for fkey in ("name", "description", "parameters"):
                if fkey not in fn:
                    failures.append(f"function missing '{fkey}'")
            if not callable(tool.get("func")):
                failures.append(f"{fn.get('name')}: func not callable")
            if not inspect.iscoroutinefunction(tool.get("func")):
                failures.append(f"{fn.get('name')}: func not async")
            names.add(fn.get("name"))

        expected_names = {"schedule_task", "schedule_self_task", "list_tasks", "cancel_task"}
        if names != expected_names:
            failures.append(f"expected names {expected_names}, got {names}")

    if failures:
        print("  [FAIL]\n  " + "\n  ".join(failures))
        return False
    print("  [PASS]")
    return True


def test_schedule_task_required_params():
    """schedule_task schema requires 'prompt' and 'run_at'."""
    print("Test: schedule_task required params")
    tool = next(t for t in get_tool() if t["function"]["name"] == "schedule_task")
    required = tool["function"]["parameters"].get("required", [])
    if "prompt" not in required or "run_at" not in required:
        print(f"  [FAIL] required={required}")
        return False
    print("  [PASS]")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic loader — list return from get_tool()
# ─────────────────────────────────────────────────────────────────────────────

def test_tool_loader_flattens_list():
    """ToolLoader.load_tools() flattens list returns from get_tool()."""
    print("Test: ToolLoader handles list return from get_tool()")
    from lib.utils.dynamic_loader import ToolLoader

    loader = ToolLoader()
    results = [loader._process_tool(t) for t in get_tool()]

    failures = []
    if len(results) != 4:
        failures.append(f"expected 4 processed tools, got {len(results)}")
    for r in results:
        if r.get("type") != "function":
            failures.append(f"missing type=function: {r}")
        if "func" not in r:
            failures.append(f"missing func: {r}")

    if failures:
        print("  [FAIL]\n  " + "\n  ".join(failures))
        return False
    print("  [PASS]")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Origin field
# ─────────────────────────────────────────────────────────────────────────────

def test_scheduled_task_defaults_origin_user():
    """New ScheduledTask dataclass defaults origin to 'user'."""
    print("Test: ScheduledTask defaults origin='user'")
    t = ScheduledTask(
        id="x", prompt="p", scheduled_at=1, created_at=1,
        user_id="u", conversation_id="c", adapter="none", status="pending",
    )
    assert t.origin == "user", f"expected 'user', got {t.origin!r}"
    assert t.ttl_runs is None, f"expected None, got {t.ttl_runs!r}"
    print("  [PASS]")

def test_create_task_with_origin_ai():
    """create_task persists origin='ai' and ttl_runs in DB."""
    print("Test: create_task with origin='ai' and ttl_runs")
    scheduler, db_path = _tmp_scheduler()

    async def run():
        await scheduler.start()
        task = await scheduler.create_task(
            prompt="ai task", scheduled_at=int(time.time()) + 9999,
            user_id="discord:100", conversation_id="chan:200",
            adapter="discord", origin="ai", ttl_runs=5, label="ai-test",
        )
        assert task.origin == "ai"
        assert task.ttl_runs == 5

        # Verify in DB
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT origin, ttl_runs FROM scheduled_tasks WHERE id=?",
                                  (task.id,)) as cur:
                row = await cur.fetchone()
                assert row["origin"] == "ai"
                assert row["ttl_runs"] == 5
        await scheduler.stop()
    asyncio.run(run())
    os.unlink(db_path)
    print("  [PASS]")

def test_create_task_max_self_tasks():
    """create_task enforces MAX_SELF_TASKS for origin='ai'."""
    print("Test: MAX_SELF_TASKS enforcement")
    scheduler, db_path = _tmp_scheduler()

    async def run():
        await scheduler.start()
        # Fill up to MAX_SELF_TASKS
        for i in range(MAX_SELF_TASKS):
            await scheduler.create_task(
                prompt=f"ai task {i}",
                scheduled_at=int(time.time()) + 9999 + i,
                user_id="discord:100", conversation_id="chan:200",
                adapter="none", origin="ai",
            )
        # Next one should fail
        try:
            await scheduler.create_task(
                prompt="overflow",
                scheduled_at=int(time.time()) + 9999,
                user_id="discord:100", conversation_id="chan:200",
                adapter="none", origin="ai",
            )
            assert False, "expected ValueError"
        except ValueError as e:
            assert "20" in str(e) and "AI self-scheduled" in str(e)
        await scheduler.stop()
    asyncio.run(run())
    os.unlink(db_path)
    print("  [PASS]")

def test_origin_user_does_not_count_toward_self_limit():
    """User-origin tasks do NOT count toward MAX_SELF_TASKS."""
    print("Test: user tasks don't count toward MAX_SELF_TASKS")
    scheduler, db_path = _tmp_scheduler()

    async def run():
        await scheduler.start()
        # Create MAX_SELF_TASKS user tasks — should be fine
        for i in range(MAX_SELF_TASKS):
            await scheduler.create_task(
                prompt=f"user task {i}",
                scheduled_at=int(time.time()) + 9999 + i,
                user_id="discord:100", conversation_id="chan:200",
                adapter="none", origin="user",
            )
        # Then create MAX_SELF_TASKS AI tasks — should also be fine
        for i in range(MAX_SELF_TASKS):
            await scheduler.create_task(
                prompt=f"ai task {i}",
                scheduled_at=int(time.time()) + 9999 + i,
                user_id="discord:100", conversation_id="chan:200",
                adapter="none", origin="ai",
            )
        # Next AI task should fail
        try:
            await scheduler.create_task(
                prompt="overflow",
                scheduled_at=int(time.time()) + 9999,
                user_id="discord:100", conversation_id="chan:200",
                adapter="none", origin="ai",
            )
            assert False, "expected ValueError"
        except ValueError:
            pass
        await scheduler.stop()
    asyncio.run(run())
    os.unlink(db_path)
    print("  [PASS]")

def test_row_to_task_includes_origin():
    """_row_to_task populates origin and ttl_runs from DB row."""
    print("Test: _row_to_task includes origin/ttl_runs")
    from lib.services.task_scheduler import _row_to_task

    row = {
        "id": "x", "label": "l", "prompt": "p",
        "scheduled_at": 1, "created_at": 2,
        "user_id": "u", "conversation_id": "c",
        "adapter": "none", "status": "pending",
        "result": None, "recurrence": None,
        "recurrence_interval": None, "context_messages": 10,
        "last_fired_at": None,
        "origin": "ai", "ttl_runs": 3,
    }
    t = _row_to_task(row)
    assert t.origin == "ai"
    assert t.ttl_runs == 3
    print("  [PASS]")

def test_row_to_task_falls_back_origin():
    """_row_to_task falls back to 'user' origin for rows without the column."""
    print("Test: _row_to_task falls back origin='user'")
    from lib.services.task_scheduler import _row_to_task

    row = {
        "id": "x", "label": "l", "prompt": "p",
        "scheduled_at": 1, "created_at": 2,
        "user_id": "u", "conversation_id": "c",
        "adapter": "none", "status": "pending",
        "result": None, "recurrence": None,
        "recurrence_interval": None, "context_messages": 10,
        "last_fired_at": None,
    }
    t = _row_to_task(row)
    assert t.origin == "user"
    assert t.ttl_runs is None
    print("  [PASS]")


# ─────────────────────────────────────────────────────────────────────────────
# TTL enforcement
# ─────────────────────────────────────────────────────────────────────────────

def test_ttl_runs_decrements():
    """After a recurring task fires, ttl_runs decrements in DB."""
    print("Test: ttl_runs decrements after task fire")
    scheduler, db_path = _tmp_scheduler()
    event_bus = EventBus()

    async def run():
        await scheduler.start()
        now = int(time.time())

        scheduler._event_bus = event_bus
        event_bus.subscribe("task.completed", scheduler._on_task_completed)

        task = await scheduler.create_task(
            prompt="ttl test", scheduled_at=now - 1,  # overdue
            user_id="discord:100", conversation_id="chan:200",
            adapter="none", recurrence="hourly",
            origin="ai", ttl_runs=3,
        )

        # Mark as running (as _fire_overdue would)
        await scheduler._set_status(task.id, "running")

        # Simulate task.completed event
        await event_bus.publish(Event(
            type="task.completed",
            data={
                "task_id": task.id,
                "result": "done",
                "success": True,
                "adapter": "none",
                "recurrence": "hourly",
                "ttl_runs": 3,
            },
        ))

        # After the run, ttl_runs should be 2 (decremented before reschedule)
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT ttl_runs, status FROM scheduled_tasks WHERE id=?",
                                  (task.id,)) as cur:
                row = await cur.fetchone()
                assert row["ttl_runs"] == 2, f"expected ttl_runs=2, got {row['ttl_runs']}"
                assert row["status"] == "pending"  # still runs more

        await scheduler.stop()
    asyncio.run(run())
    os.unlink(db_path)
    print("  [PASS]")

def test_ttl_runs_expires():
    """When ttl_runs reaches 1, the task is marked done and not rescheduled."""
    print("Test: ttl_runs expiry marks task done")
    scheduler, db_path = _tmp_scheduler()
    event_bus = EventBus()

    async def run():
        await scheduler.start()
        now = int(time.time())

        scheduler._event_bus = event_bus
        event_bus.subscribe("task.completed", scheduler._on_task_completed)

        task = await scheduler.create_task(
            prompt="last run", scheduled_at=now - 1,
            user_id="discord:100", conversation_id="chan:200",
            adapter="none", recurrence="hourly",
            origin="ai", ttl_runs=1,  # final run
        )

        await scheduler._set_status(task.id, "running")

        await event_bus.publish(Event(
            type="task.completed",
            data={
                "task_id": task.id,
                "result": "done",
                "success": True,
                "adapter": "none",
                "recurrence": "hourly",
                "ttl_runs": 1,
            },
        ))

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT ttl_runs, status, result FROM scheduled_tasks WHERE id=?",
                                  (task.id,)) as cur:
                row = await cur.fetchone()
                assert row["status"] == "done", f"expected done, got {row['status']}"
                assert row["result"] == "ttl_expired", f"expected ttl_expired, got {row['result']}"

        await scheduler.stop()
    asyncio.run(run())
    os.unlink(db_path)
    print("  [PASS]")

def test_no_ttl_reschedules_normally():
    """Tasks without ttl_runs reschedule normally (no TTL logic triggered)."""
    print("Test: no ttl_runs reschedules normally")
    scheduler, db_path = _tmp_scheduler()
    event_bus = EventBus()

    async def run():
        await scheduler.start()
        now = int(time.time())

        scheduler._event_bus = event_bus
        event_bus.subscribe("task.completed", scheduler._on_task_completed)

        task = await scheduler.create_task(
            prompt="forever task", scheduled_at=now - 1,
            user_id="discord:100", conversation_id="chan:200",
            adapter="none", recurrence="hourly",
            origin="ai", ttl_runs=None,
        )

        await scheduler._set_status(task.id, "running")

        await event_bus.publish(Event(
            type="task.completed",
            data={
                "task_id": task.id,
                "result": "done",
                "success": True,
                "adapter": "none",
                "recurrence": "hourly",
                "ttl_runs": None,
            },
        ))

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT status FROM scheduled_tasks WHERE id=?",
                                  (task.id,)) as cur:
                row = await cur.fetchone()
                assert row["status"] == "pending"  # rescheduled

        await scheduler.stop()
    asyncio.run(run())
    os.unlink(db_path)
    print("  [PASS]")


# ─────────────────────────────────────────────────────────────────────────────
# schedule_self_task tool
# ─────────────────────────────────────────────────────────────────────────────

def test_schedule_self_task_tool_success():
    """schedule_self_task tool returns success with origin='ai'."""
    print("Test: schedule_self_task tool — success path")
    from OllamaTools.schedule_task import schedule_self_task

    mock_task = ScheduledTask(
        id="self-abc-123", label="self-test", prompt="hello",
        scheduled_at=int(time.time()) + 3600, created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:42",
        adapter="discord", status="pending",
        origin="ai", ttl_runs=5,
    )
    mock_scheduler = MagicMock()
    mock_scheduler.create_task = AsyncMock(return_value=mock_task)

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=mock_scheduler):
            result = await schedule_self_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="daily weather check",
                run_at="0 9 * * *",
                label="Morning Weather",
                recurrence="0 9 * * *",
                ttl_runs=5,
            )
        data = json.loads(result)
        assert data["success"] is True, f"expected success: {data}"
        assert data["data"]["task_id"] == "self-abc-123"
        assert data["data"]["origin"] == "ai"
        assert data["data"]["ttl_runs"] == 5
        # Verify scheduler was called with origin='ai'
        call_kwargs = mock_scheduler.create_task.call_args.kwargs
        assert call_kwargs["origin"] == "ai"
        assert call_kwargs["ttl_runs"] == 5
    asyncio.run(run())
    print("  [PASS]")

def test_schedule_self_task_tool_notify_target():
    """schedule_self_task with notify_target overrides adapter."""
    print("Test: schedule_self_task — notify_target override")
    from OllamaTools.schedule_task import schedule_self_task

    mock_task = ScheduledTask(
        id="self-xyz", label="notify-test", prompt="hi",
        scheduled_at=int(time.time()) + 9999, created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:999",
        adapter="discord", status="pending",
        origin="ai", ttl_runs=None,
    )
    mock_scheduler = MagicMock()
    mock_scheduler.create_task = AsyncMock(return_value=mock_task)

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=mock_scheduler):
            await schedule_self_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="notify test",
                run_at="in 1 hour",
                notify_target="discord:special_channel",
            )
        call_kwargs = mock_scheduler.create_task.call_args.kwargs
        assert call_kwargs["adapter"] == "discord"
        assert call_kwargs["conversation_id"] == "special_channel"
    asyncio.run(run())
    print("  [PASS]")

def test_schedule_self_task_zero_ttl():
    """schedule_self_task with ttl_runs=0 stores None (unlimited)."""
    print("Test: schedule_self_task — ttl_runs=0 → None")
    from OllamaTools.schedule_task import schedule_self_task

    mock_task = ScheduledTask(
        id="self-zzz", label="no-ttl", prompt="hi",
        scheduled_at=int(time.time()) + 9999, created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:42",
        adapter="none", status="pending",
        origin="ai", ttl_runs=None,
    )
    mock_scheduler = MagicMock()
    mock_scheduler.create_task = AsyncMock(return_value=mock_task)

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=mock_scheduler):
            await schedule_self_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="unlimited",
                run_at="in 1 hour",
                ttl_runs=0,
            )
        call_kwargs = mock_scheduler.create_task.call_args.kwargs
        assert call_kwargs["ttl_runs"] is None
    asyncio.run(run())
    print("  [PASS]")


# ─────────────────────────────────────────────────────────────────────────────
# Tool functions — mocked scheduler
# ─────────────────────────────────────────────────────────────────────────────

def test_schedule_task_tool_success():
    """schedule_task tool returns success with task_id when valid."""
    print("Test: schedule_task tool — success path")
    from OllamaTools.schedule_task import schedule_task

    mock_task = ScheduledTask(
        id="abc-123", label="test", prompt="hello",
        scheduled_at=int(time.time()) + 3600, created_at=int(time.time()),
        user_id="discord:123", conversation_id="chan:42",
        adapter="discord", status="pending",
    )
    mock_scheduler = MagicMock()
    mock_scheduler.create_task = AsyncMock(return_value=mock_task)

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=mock_scheduler):
            result = await schedule_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="do a thing",
                run_at="in 1 hour",
            )
        data = json.loads(result)
        assert data["success"] is True, f"expected success: {data}"
        assert data["data"]["task_id"] == "abc-123"
    asyncio.run(run())
    print("  [PASS]")


def test_schedule_task_tool_past_time():
    """schedule_task tool rejects a time in the past."""
    print("Test: schedule_task tool — past time rejected")
    from OllamaTools.schedule_task import schedule_task

    async def run():
        result = await schedule_task(
            client=_make_client(),
            source_user=_make_source_user(),
            prompt="too late",
            run_at="1 hour ago",
        )
        data = json.loads(result)
        assert data["success"] is False, f"expected failure: {data}"
    asyncio.run(run())
    print("  [PASS]")


def test_schedule_task_tool_cron_auto_recurrence():
    """schedule_task tool auto-sets recurrence when run_at is a cron expression."""
    print("Test: schedule_task tool — cron in run_at auto-sets recurrence")
    try:
        from croniter import croniter
    except ImportError:
        print("  [SKIP] croniter not installed")
        return True

    from OllamaTools.schedule_task import schedule_task

    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        from lib.services.task_scheduler import ScheduledTask
        return ScheduledTask(
            id="abc", label=None, prompt=kwargs["prompt"],
            scheduled_at=kwargs["scheduled_at"], created_at=int(time.time()),
            user_id=kwargs["user_id"], conversation_id=kwargs["conversation_id"],
            adapter=kwargs["adapter"], status="pending",
            recurrence=kwargs.get("recurrence"),
        )

    mock_scheduler = MagicMock()
    mock_scheduler.create_task = AsyncMock(side_effect=fake_create)

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=mock_scheduler):
            result = await schedule_task(
                client=_make_client(),
                source_user=_make_source_user(),
                prompt="daily check",
                run_at="0 9 * * 1-5",  # weekdays at 9am
                recurrence="",         # not explicitly set
            )
        data = json.loads(result)
        assert data["success"] is True, f"expected success: {data}"
        # recurrence should be the cron expression
        assert captured.get("recurrence") == "0 9 * * 1-5", (
            f"expected recurrence='0 9 * * 1-5', got {captured.get('recurrence')!r}"
        )
    asyncio.run(run())
    print("  [PASS]")


def test_list_tasks_tool():
    """list_tasks tool returns success with empty list."""
    print("Test: list_tasks tool — returns task list")
    from OllamaTools.schedule_task import list_tasks

    mock_scheduler = MagicMock()
    mock_scheduler.list_tasks = AsyncMock(return_value=[])

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=mock_scheduler):
            result = await list_tasks(client=_make_client(), source_user=_make_source_user())
        data = json.loads(result)
        assert data["success"] is True and data["data"] == []
    asyncio.run(run())
    print("  [PASS]")


def test_cancel_task_tool_success():
    """cancel_task tool returns success when task is cancelled."""
    print("Test: cancel_task tool — success path")
    from OllamaTools.schedule_task import cancel_task

    mock_scheduler = MagicMock()
    mock_scheduler.cancel_task = AsyncMock(return_value=True)

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=mock_scheduler):
            result = await cancel_task(
                client=_make_client(), source_user=_make_source_user(), task_id="abc-123"
            )
        data = json.loads(result)
        assert data["success"] is True
    asyncio.run(run())
    print("  [PASS]")


def test_cancel_task_tool_not_found():
    """cancel_task tool returns failure when task not found."""
    print("Test: cancel_task tool — not found")
    from OllamaTools.schedule_task import cancel_task

    mock_scheduler = MagicMock()
    mock_scheduler.cancel_task = AsyncMock(return_value=False)

    async def run():
        with patch("OllamaTools.schedule_task._get_scheduler", return_value=mock_scheduler):
            result = await cancel_task(
                client=_make_client(), source_user=_make_source_user(), task_id="nope"
            )
        data = json.loads(result)
        assert data["success"] is False
    asyncio.run(run())
    print("  [PASS]")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))