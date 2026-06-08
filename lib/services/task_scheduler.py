"""
task_scheduler.py — AI self-scheduling service.

Allows the AI to schedule prompts to run at a future time.
Tasks are persisted in SQLite (scheduler.db), a background asyncio
poller fires overdue tasks through ai_pipeline, and results are
delivered back to the originating channel / adapter.

Schema
------
scheduled_tasks:
  id                  TEXT PK  — uuid4
  label               TEXT     — optional human-readable name
  prompt              TEXT     — prompt to run when time comes
  scheduled_at        INTEGER  — unix timestamp UTC
  created_at          INTEGER  — unix timestamp UTC
  last_fired_at       INTEGER  — unix timestamp of last successful fire (for restart recovery)
  user_id             TEXT     — scoped user ID (e.g. discord:123)
  conversation_id     TEXT     — delivery target (channel/group)
  adapter             TEXT     — 'discord' | 'whatsapp' | 'none'
  status              TEXT     — 'pending' | 'running' | 'done' | 'failed'
  result              TEXT     — stored after execution
  recurrence          TEXT     — NULL | 'daily' | 'hourly' | 'every_n_hours' | 'every_n_days'
                                 OR a 5-field cron expression (e.g. '30 9 * * 1-5')
  recurrence_interval INTEGER  — N for interval-based recurrence
  context_messages    INTEGER  — history messages to include at runtime (default 10)
"""

import asyncio
import datetime
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import aiosqlite

from lib.services.event_bus import Event

MAX_PENDING_PER_USER = 50
MAX_SELF_TASKS = 20       # AI-created tasks cap
POLL_INTERVAL = 30  # seconds

_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400

# Matches a 5-field cron expression: "MIN HOUR DOM MON DOW"
_CRON_RE = re.compile(r'^\S+\s+\S+\s+\S+\s+\S+\s+\S+$')


def _is_cron(s: str) -> bool:
    """Return True if s looks like a 5-field cron expression."""
    return bool(_CRON_RE.match(s.strip())) if s else False


@dataclass
class TaskCreateOptions:
    """Optional parameters for TaskScheduler.create_task()."""
    adapter: str = "none"
    label: Optional[str] = None
    recurrence: Optional[str] = None
    recurrence_interval: Optional[int] = None
    context_messages: int = 10
    origin: str = "user"
    ttl_runs: Optional[int] = None


@dataclass
class ScheduledTask:
    """In-memory representation of a scheduled task row."""
    id: str
    prompt: str
    scheduled_at: int
    created_at: int
    user_id: str
    conversation_id: str
    adapter: str
    status: str
    label: Optional[str] = None
    result: Optional[str] = None
    recurrence: Optional[str] = None
    recurrence_interval: Optional[int] = None
    context_messages: int = 10
    last_fired_at: Optional[int] = None
    origin: str = "user"        # 'user' or 'ai'
    ttl_runs: Optional[int] = None  # remaining runs for AI self-tasks (None = unlimited)


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id                  TEXT    PRIMARY KEY,
    label               TEXT,
    prompt              TEXT    NOT NULL,
    scheduled_at        INTEGER NOT NULL,
    created_at          INTEGER NOT NULL,
    last_fired_at       INTEGER,
    user_id             TEXT    NOT NULL,
    conversation_id     TEXT    NOT NULL,
    adapter             TEXT    NOT NULL DEFAULT 'none',
    status              TEXT    NOT NULL DEFAULT 'pending',
    result              TEXT,
    recurrence          TEXT,
    recurrence_interval INTEGER,
    context_messages    INTEGER NOT NULL DEFAULT 10,
    origin              TEXT    NOT NULL DEFAULT 'user',
    ttl_runs            INTEGER
)
"""

# Migrations for pre-existing DBs.
_MIGRATE_LAST_FIRED_AT = (
    "ALTER TABLE scheduled_tasks ADD COLUMN last_fired_at INTEGER"
)
_MIGRATE_ORIGIN = (
    "ALTER TABLE scheduled_tasks ADD COLUMN origin TEXT NOT NULL DEFAULT 'user'"
)
_MIGRATE_TTL_RUNS = (
    "ALTER TABLE scheduled_tasks ADD COLUMN ttl_runs INTEGER"
)


class TaskScheduler:
    """
    Background service that persists and fires AI-scheduled tasks.

    Usage::

        scheduler = TaskScheduler("scheduler.db")
        await scheduler.start()          # starts background poll loop
        ...
        await scheduler.stop()           # clean shutdown
    """

    def __init__(self, db_path: str = "scheduler.db", event_bus: Any = None):
        self._db_path = db_path
        self._event_bus = event_bus
        self._conn: Optional[aiosqlite.Connection] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ──────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────

    async def _ensure_conn(self) -> aiosqlite.Connection:
        """Return the persistent connection, opening it lazily if needed."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
        return self._conn

    async def start(self) -> None:
        """Open DB, ensure schema, run migrations, check missed tasks, then start poll loop."""
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute(_CREATE_TABLE)
        await self._conn.commit()
        # Run all column migrations (idempotent — skip if column exists)
        for migration in (
            _MIGRATE_LAST_FIRED_AT,
            _MIGRATE_ORIGIN,
            _MIGRATE_TTL_RUNS,
        ):
            try:
                await self._conn.execute(migration)
                await self._conn.commit()
            except aiosqlite.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    pass  # Column already exists
                else:
                    raise

        # Handle missed tasks BEFORE starting the poll loop to avoid a race
        # where _fire_overdue() picks up the same tasks as the missed-task check.
        if self._event_bus:
            self._event_bus.subscribe("task.completed", self._on_task_completed)

        await self._check_missed_on_startup()

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logging.info(f"TaskScheduler started (db={self._db_path}, poll={POLL_INTERVAL}s)")

    async def stop(self) -> None:
        """Stop the background poll loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._conn:
            await self._conn.close()
        logging.info("TaskScheduler stopped")

    # ──────────────────────────────────────────────────────────────────────
    # Public CRUD — called by tools
    # ──────────────────────────────────────────────────────────────────────

    async def create_task(
        self,
        prompt: str,
        scheduled_at: int,
        user_id: str,
        conversation_id: str,
        options: TaskCreateOptions | None = None,
    ) -> ScheduledTask:
        """
        Persist a new scheduled task.

        Args:
            prompt:           What the AI should do when the task fires.
            scheduled_at:     Unix timestamp (UTC) when to run.
            user_id:          Scoped user ID (e.g. 'discord:12345').
            conversation_id:  Channel / group ID for delivery.
            options:          TaskCreateOptions with optional parameters
                              (adapter, label, recurrence, etc.).

        Returns:
            The created ScheduledTask.

        Raises:
            ValueError: If limits are exceeded (MAX_PENDING_PER_USER or MAX_SELF_TASKS).
        """
        if options is None:
            options = TaskCreateOptions()

        db = await self._ensure_conn()
        # Enforce per-user limit
        async with db.execute(
            "SELECT COUNT(*) FROM scheduled_tasks WHERE user_id = ? AND status = 'pending'",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            if row and row[0] >= MAX_PENDING_PER_USER:
                raise ValueError(
                    f"You already have {MAX_PENDING_PER_USER} pending tasks — "
                    "cancel some before scheduling more."
                )

        # Enforce AI self-task limit
        if options.origin == "ai":
            async with db.execute(
                "SELECT COUNT(*) FROM scheduled_tasks WHERE origin = 'ai' AND status = 'pending'",
            ) as cur:
                row = await cur.fetchone()
                if row and row[0] >= MAX_SELF_TASKS:
                    raise ValueError(
                        f"Maximum {MAX_SELF_TASKS} AI self-scheduled tasks reached — "
                        "cancel some before scheduling more."
                    )

        task_id = str(uuid.uuid4())
        now = int(time.time())
        await db.execute(
            """INSERT INTO scheduled_tasks
               (id, label, prompt, scheduled_at, created_at, user_id,
                conversation_id, adapter, status, recurrence,
                recurrence_interval, context_messages, origin, ttl_runs)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, options.label, prompt, scheduled_at, now,
                user_id, conversation_id, options.adapter, "pending",
                options.recurrence, options.recurrence_interval,
                options.context_messages, options.origin, options.ttl_runs,
            ),
        )
        await db.commit()

        return ScheduledTask(
            id=task_id, label=options.label, prompt=prompt,
            scheduled_at=scheduled_at, created_at=now,
            user_id=user_id, conversation_id=conversation_id,
            adapter=options.adapter, status="pending",
            recurrence=options.recurrence,
            recurrence_interval=options.recurrence_interval,
            context_messages=options.context_messages,
            origin=options.origin, ttl_runs=options.ttl_runs,
        )

    async def list_tasks(self, user_id: str) -> list[ScheduledTask]:
        """Return all non-done tasks for a user, ordered by scheduled_at."""
        db = await self._ensure_conn()
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(
                """SELECT * FROM scheduled_tasks
                   WHERE user_id = ? AND status NOT IN ('done','failed')
                   ORDER BY scheduled_at ASC""",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
        finally:
            db.row_factory = None
        return [_row_to_task(r) for r in rows]

    async def cancel_task(self, task_id: str, user_id: str) -> bool:
        """
        Cancel a pending task owned by user_id.

        Returns:
            True if cancelled, False if not found / already running.
        """
        db = await self._ensure_conn()
        cur = await db.execute(
            """UPDATE scheduled_tasks SET status = 'failed', result = 'cancelled by user'
               WHERE id = ? AND user_id = ? AND status = 'pending'""",
            (task_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0

    # ──────────────────────────────────────────────────────────────────────
    # Internal polling — publishes task.due, subscribes to task.completed
    # ──────────────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Polls DB every POLL_INTERVAL seconds and fires overdue tasks."""
        while self._running:
            try:
                await self._fire_overdue()
            except Exception as e:
                logging.error(f"TaskScheduler poll error: {e}", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)

    async def _fire_overdue(self) -> None:
        """Find overdue tasks and publish task.due for each."""
        now = int(time.time())
        db = await self._ensure_conn()
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(
                "SELECT * FROM scheduled_tasks WHERE status = 'pending' AND scheduled_at <= ?",
                (now,),
            ) as cur:
                rows = await cur.fetchall()
        finally:
            db.row_factory = None

        for row in rows:
            task = _row_to_task(row)
            logging.info(
                f"TaskScheduler firing task {task.id!r} ({task.label!r}): {task.prompt[:80]}"
            )
            await self._set_status(task.id, "running")

            if self._event_bus:
                await self._event_bus.publish(Event(
                    type="task.due",
                    data={
                        "task_id": task.id,
                        "label": task.label,
                        "prompt": task.prompt,
                        "user_id": task.user_id,
                        "conversation_id": task.conversation_id,
                        "context_messages": task.context_messages,
                        "adapter": task.adapter,
                        "recurrence": task.recurrence,
                        "ttl_runs": task.ttl_runs,
                    },
                ))
            else:
                logging.warning(
                    f"TaskScheduler: no event_bus configured — "
                    f"task {task.id!r} will not be executed"
                )

    async def _on_task_completed(self, event: Event) -> None:
        """Handle task.completed event: store result, reschedule if recurring.

        Background tasks (research, dream, etc.) also fire ``task.completed`` but
        carry ``task_type`` instead of ``task_id`` — ignore those here; they are
        handled by TaskNotificationQueue and NotificationPipeline.
        """
        data = event.data
        task_id: str | None = data.get("task_id")
        if task_id is None:
            return  # Background-task notification (research/dream), not a scheduled task
        success: bool = data.get("success", True)
        result_text: str = data.get("result", "")
        recurrence: Optional[str] = data.get("recurrence")
        ttl_runs: Optional[int] = data.get("ttl_runs")

        logging.info(
            f"TaskScheduler task {task_id!r} "
            f"{'done' if success else 'failed'}"
        )

        fired_at = int(time.time())
        if success:
            await self._set_status(
                task_id, "done", result=result_text, last_fired_at=fired_at
            )
        else:
            await self._set_status(task_id, "failed", result=result_text)

        # Reschedule recurring tasks, handling TTL for AI self-tasks
        if recurrence:
            if ttl_runs is not None:
                if ttl_runs <= 1:
                    await self._set_status(task_id, "done", result="ttl_expired")
                    logging.info(
                        f"TaskScheduler task {task_id!r} TTL expired after final run"
                    )
                else:
                    await self._decrement_ttl(task_id, ttl_runs - 1)
                    await self._reschedule_by_id(task_id, recurrence)
            else:
                await self._reschedule_by_id(task_id, recurrence)

    async def _reschedule(self, task: ScheduledTask) -> None:
        """Compute the next scheduled_at for a recurring task and reset it to pending."""
        next_at = _next_run(task)
        if next_at is None:
            return

        db = await self._ensure_conn()
        await db.execute(
            """UPDATE scheduled_tasks
               SET status = 'pending', scheduled_at = ?, result = NULL
               WHERE id = ?""",
            (next_at, task.id),
        )
        await db.commit()
        logging.info(
            f"TaskScheduler: rescheduled {task.id!r} to {next_at} "
            f"(recurrence={task.recurrence})"
        )

    async def _reschedule_by_id(self, task_id: str, recurrence: str) -> None:
        """Reschedule a task by ID and recurrence (for event-driven reschedule)."""
        db = await self._ensure_conn()
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
            ) as cur:
                row = await cur.fetchone()
        finally:
            db.row_factory = None
        if not row:
            logging.warning(
                f"TaskScheduler: cannot reschedule — task {task_id!r} not found"
            )
            return

        task = _row_to_task(row)
        await self._reschedule(task)

    async def _set_status(
        self,
        task_id: str,
        status: str,
        result: Optional[str] = None,
        last_fired_at: Optional[int] = None,
    ) -> None:
        db = await self._ensure_conn()
        await db.execute(
            """UPDATE scheduled_tasks
               SET status = ?, result = ?,
                   last_fired_at = COALESCE(?, last_fired_at)
               WHERE id = ?""",
            (status, result, last_fired_at, task_id),
        )
        await db.commit()

    async def _decrement_ttl(self, task_id: str, new_ttl: int) -> None:
        """Decrement the ttl_runs counter for a self-scheduled task."""
        db = await self._ensure_conn()
        await db.execute(
            "UPDATE scheduled_tasks SET ttl_runs = ? WHERE id = ?",
            (new_ttl, task_id),
        )
        await db.commit()

    # ──────────────────────────────────────────────────────────────────────
    # Startup: missed-task detection
    # ──────────────────────────────────────────────────────────────────────

    async def _check_missed_on_startup(self) -> None:
        """
        Detect tasks whose scheduled_at passed while the scheduler was offline.

        One-shots: mark as failed, deliver a 'you missed this' notification to the user.
        Recurring: silently rebase next-fire from now so they don't pile up.
        """
        now = int(time.time())
        db = await self._ensure_conn()
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(
                "SELECT * FROM scheduled_tasks WHERE status = 'pending' AND scheduled_at < ?",
                (now,),
            ) as cur:
                rows = await cur.fetchall()
        finally:
            db.row_factory = None

        if not rows:
            return

        missed_one_shots: list[ScheduledTask] = []
        for row in rows:
            task = _row_to_task(row)
            if task.recurrence:
                # Recurring: rebase next-fire from now instead of silently firing
                await self._rebase_recurring(task, from_ts=now)
            else:
                # One-shot: mark missed, collect for notification
                await self._set_status(task.id, "failed", result="missed_offline")
                missed_one_shots.append(task)

        if missed_one_shots:
            await self._notify_missed(missed_one_shots)
            logging.info(
                f"TaskScheduler: {len(missed_one_shots)} missed one-shot task(s) "
                "notified to users"
            )

    async def _rebase_recurring(self, task: ScheduledTask, from_ts: int) -> None:
        """
        Advance a recurring task's next-fire to the first future occurrence
        relative to from_ts (used after an offline period).
        """
        next_at = _next_run_from(task, from_ts)
        if next_at is None:
            return
        db = await self._ensure_conn()
        await db.execute(
            "UPDATE scheduled_tasks SET scheduled_at = ? WHERE id = ?",
            (next_at, task.id),
        )
        await db.commit()
        logging.info(f"TaskScheduler: rebased recurring task {task.id!r} → {next_at}")

    async def _notify_missed(self, tasks: list[ScheduledTask]) -> None:
        """
        Deliver a grouped 'missed while offline' notification to each unique
        (conversation_id, adapter) combination.
        """
        if not self._event_bus:
            return

        by_conv: dict[tuple[str, str], list[ScheduledTask]] = {}
        for t in tasks:
            key = (t.conversation_id, t.adapter)
            by_conv.setdefault(key, []).append(t)

        for (conv_id, adapter), group in by_conv.items():
            if adapter == "none":
                continue
            notification = _build_missed_notification(group)
            await self._event_bus.publish(Event(
                type="task.missed",
                data={
                    "notification": notification,
                    "conversation_id": conv_id,
                    "adapter": adapter,
                },
            ))


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _row_to_task(row) -> ScheduledTask:
    """Convert a DB row (dict-like) to a ScheduledTask dataclass."""
    return ScheduledTask(
        id=row["id"],
        label=row["label"],
        prompt=row["prompt"],
        scheduled_at=row["scheduled_at"],
        created_at=row["created_at"],
        last_fired_at=row["last_fired_at"],
        user_id=row["user_id"],
        conversation_id=row["conversation_id"],
        adapter=row["adapter"],
        status=row["status"],
        result=row["result"],
        recurrence=row["recurrence"],
        recurrence_interval=row["recurrence_interval"],
        context_messages=row["context_messages"] or 10,
        origin=row["origin"] if "origin" in row.keys() else "user",
        ttl_runs=row["ttl_runs"] if "ttl_runs" in row.keys() else None,
    )


def _next_run(task: ScheduledTask) -> Optional[int]:
    """
    Compute the next unix timestamp for a recurring task.

    Uses last_fired_at as the anchor when available so that restarts
    reconstruct the correct next-fire window instead of re-anchoring
    from the original scheduled_at.

    Returns:
        Next scheduled_at or None if recurrence pattern is unrecognised.
    """
    anchor = task.last_fired_at or task.scheduled_at
    return _next_run_from(task, anchor)


def _next_run_from(task: ScheduledTask, from_ts: int) -> Optional[int]:
    """
    Compute next run time for *task* relative to *from_ts*.

    Handles both 5-field cron expressions and simple interval keywords.
    For cron expressions, returns the next occurrence strictly after from_ts.

    Args:
        task:    The recurring ScheduledTask.
        from_ts: Unix timestamp to compute the next occurrence from.

    Returns:
        Next scheduled_at or None if pattern is unrecognised.
    """
    r = task.recurrence
    n = task.recurrence_interval or 1

    if not r:
        return None

    if _is_cron(r):
        try:
            from croniter import croniter
            cron = croniter(r, from_ts)
            return int(cron.get_next(float))
        except Exception as e:
            logging.warning(f"TaskScheduler: invalid cron expression {r!r}: {e}")
            return None

    if r == "hourly":
        return from_ts + _SECONDS_PER_HOUR
    if r == "daily":
        return from_ts + _SECONDS_PER_DAY
    if r == "every_n_hours":
        return from_ts + n * _SECONDS_PER_HOUR
    if r == "every_n_days":
        return from_ts + n * _SECONDS_PER_DAY

    logging.warning(f"TaskScheduler: unknown recurrence '{r}' for task {task.id}")
    return None


def _build_missed_notification(tasks: list[ScheduledTask]) -> str:
    """
    Build a human-readable 'missed tasks' notification string.

    Args:
        tasks: One or more one-shot tasks that were missed while offline.

    Returns:
        Formatted notification text.
    """
    lines = [
        f"⚠️ I was offline and missed {len(tasks)} scheduled task(s):",
    ]
    for t in tasks:
        ts = datetime.datetime.fromtimestamp(
            t.scheduled_at, tz=datetime.timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
        label = f" [{t.label}]" if t.label else ""
        lines.append(f"  • {ts}{label} — {t.prompt[:80]}")
    lines.append("These tasks have been cancelled. Re-schedule them if still needed.")
    return "\n".join(lines)
