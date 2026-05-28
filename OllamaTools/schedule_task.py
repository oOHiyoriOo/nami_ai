"""
schedule_task.py — AI self-scheduling tools.

Provides four tools:
  - schedule_task       : create a new scheduled task (user-initiated)
  - schedule_self_task  : AI creates its own scheduled task
  - list_tasks          : list pending/running tasks for the current user
  - cancel_task         : cancel a pending task by ID

All accept optional `client` / `source_user` positional args for
backward compatibility, but derive actual context from the pipeline's
ContextVar (pipeline_ctx) which is set before any tool call.
"""

import logging
import time
from typing import Optional

import dateparser

from lib.global_registry import g_data
from lib.services.ai_pipeline import pipeline_ctx
from lib.services.task_scheduler import _is_cron
from OllamaTools import tool_error, tool_success

# ── Recurrence aliases the AI can pass ───────────────────────────────────────
# Maps natural strings → canonical DB values
_RECURRENCE_MAP = {
    "hourly":        "hourly",
    "daily":         "daily",
    "every_n_hours": "every_n_hours",
    "every_n_days":  "every_n_days",
    # friendly aliases
    "every hour":    "hourly",
    "every day":     "daily",
    "once":          None,
    "one-shot":      None,
}


def _get_scheduler():
    """Retrieve the TaskScheduler from the global registry."""
    scheduler = g_data.get("task_scheduler")
    if not scheduler:
        raise RuntimeError("TaskScheduler not initialised — check app startup")
    return scheduler


def _parse_time(run_at: str) -> Optional[int]:
    """
    Parse a natural-language, ISO datetime, or 5-field cron string to a UTC
    unix timestamp (the *next* occurrence for cron patterns).

    Args:
        run_at: e.g. "in 30 minutes", "tomorrow at 9am", "2026-04-01T10:00",
                or a cron expression like "30 9 * * 1-5".

    Returns:
        Unix timestamp (int) or None if parsing failed.
    """
    run_at = run_at.strip()
    if _is_cron(run_at):
        try:
            from croniter import croniter
        except ImportError:
            return None  # croniter not installed; let _create_task_common report the error
        try:
            cron = croniter(run_at, time.time())
            return int(cron.get_next(float))
        except (ValueError, KeyError):
            return None  # invalid cron expression

    dt = dateparser.parse(
        run_at,
        settings={"RETURN_AS_TIMEZONE_AWARE": True, "PREFER_DATES_FROM": "future"},
    )
    if dt is None:
        return None
    return int(dt.timestamp())


# ─────────────────────────────────────────────────────────────────────────────
# Shared helper — all scheduling logic
# ─────────────────────────────────────────────────────────────────────────────

async def _create_task_common(
    *,
    prompt: str,
    run_at: str,
    label: str = "",
    recurrence: str = "",
    recurrence_interval: int = 0,
    context_messages: int = 10,
    source_user=None,
    client=None,
    origin: str = "user",
    ttl_runs: Optional[int] = None,
    notify_target: str = "",
    caller_name: str = "schedule_task",
) -> str:
    """
    Shared logic for schedule_task and schedule_self_task.

    Args:
        prompt, run_at, label, recurrence, recurrence_interval, context_messages:
            Task definition parameters.
        source_user, client: Tool call context for deriving user/conversation/adapter.
        origin: 'user' or 'ai' — controls response fields and DB storage.
        ttl_runs: Optional max runs for self-scheduled tasks.
        notify_target: Override delivery target (e.g. 'discord:channel_id', 'whatsapp:chat_id', or 'log').
        caller_name: Used in error logging to distinguish the two callers.

    Returns:
        JSON success/error string.
    """
    try:
        scheduler = _get_scheduler()

        # Parse run_at
        run_at_stripped = run_at.strip()
        scheduled_at = _parse_time(run_at_stripped)
        if scheduled_at is None:
            if _is_cron(run_at_stripped):
                return tool_error(
                    f"Could not parse cron expression: {run_at!r}. "
                    f"Is croniter installed? Try: pip install croniter",
                    run_at=run_at,
                )
            return tool_error(f"Could not parse time: {run_at!r}", run_at=run_at)
        if scheduled_at <= int(time.time()):
            return tool_error("Scheduled time is in the past — please use a future time.", run_at=run_at)

        # Resolve recurrence
        if _is_cron(run_at_stripped) and not recurrence.strip():
            rec = run_at_stripped
            rec_interval = None
        else:
            rec = _RECURRENCE_MAP.get(recurrence.lower().strip(), recurrence.lower().strip() or None)
            rec_interval = recurrence_interval if recurrence_interval > 0 else None

        # Validate resolved recurrence — unknown patterns silently become one-shot
        # because _next_run_from() returns None for unrecognised values.
        _valid_rec = set(_RECURRENCE_MAP.values()) | {None} | set(_RECURRENCE_MAP.keys())
        if rec and rec not in _valid_rec and not _is_cron(rec):
            return tool_error(
                f"Unknown recurrence pattern: {rec!r}. "
                f"Valid values: hourly, daily, every_n_hours, every_n_days, "
                f"or a 5-field cron expression (e.g. '0 9 * * 1-5').",
                recurrence=rec,
            )

        # Derive IDs from the tool call context
        user_id, conversation_id, adapter = _context_from_source(source_user, client)

        # Override adapter/conv_id if notify_target is set
        if notify_target.strip():
            if notify_target == "log":
                adapter = "none"
            elif ":" in notify_target:
                adapter, conversation_id = notify_target.split(":", 1)

        task = await scheduler.create_task(
            prompt=prompt,
            scheduled_at=scheduled_at,
            user_id=user_id,
            conversation_id=conversation_id,
            adapter=adapter,
            label=label or None,
            recurrence=rec,
            recurrence_interval=rec_interval,
            context_messages=max(1, context_messages),
            origin=origin,
            ttl_runs=ttl_runs,
        )

        import datetime
        human_time = datetime.datetime.fromtimestamp(
            task.scheduled_at, tz=datetime.timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")

        result: dict = {
            "task_id": task.id,
            "label": task.label,
            "scheduled_for": human_time,
            "recurrence": task.recurrence,
        }
        if origin != "user":
            result["origin"] = origin
        if ttl_runs is not None:
            result["ttl_runs"] = task.ttl_runs

        return tool_success(result, prompt=prompt[:80])

    except ValueError as e:
        return tool_error(str(e))
    except Exception as e:
        logging.error(f"{caller_name} error: {e}", exc_info=True)
        return tool_error(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Tool functions
# ─────────────────────────────────────────────────────────────────────────────

async def schedule_task(
    client=None,
    source_user=None,
    *,
    prompt: str,
    run_at: str,
    label: str = "",
    recurrence: str = "",
    recurrence_interval: int = 0,
    context_messages: int = 10,
) -> str:
    """
    Schedule a prompt to run at a future time.

    Args:
        client:               Adapter client (provides conversation context).
        source_user:          ChatUser with .id and platform info.
        prompt:               What the AI should do when the task fires.
        run_at:               When to run — natural language or ISO datetime.
        label:                Optional short name for the task.
        recurrence:           Repeat pattern: 'hourly', 'daily', 'every_n_hours',
                              'every_n_days', or empty for one-shot.
        recurrence_interval:  N for interval-based patterns (e.g. 3 for every 3 hours).
        context_messages:     How many history messages to include at fire time.

    Returns:
        JSON success/error string.
    """
    return await _create_task_common(
        prompt=prompt,
        run_at=run_at,
        label=label,
        recurrence=recurrence,
        recurrence_interval=recurrence_interval,
        context_messages=context_messages,
        source_user=source_user,
        client=client,
    )


async def list_tasks(client=None, source_user=None) -> str:
    """
    List all pending and running tasks for the current user.

    Args:
        client:      Adapter client.
        source_user: ChatUser whose tasks to list.

    Returns:
        JSON success/error string with task list.
    """
    try:
        scheduler = _get_scheduler()
        user_id, _, _ = _context_from_source(source_user, client)
        tasks = await scheduler.list_tasks(user_id)

        import datetime
        result = []
        for t in tasks:
            human_time = datetime.datetime.fromtimestamp(
                t.scheduled_at, tz=datetime.timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
            result.append({
                "id": t.id,
                "label": t.label or "(no label)",
                "prompt": t.prompt[:80],
                "scheduled_for": human_time,
                "status": t.status,
                "recurrence": t.recurrence,
                "origin": getattr(t, "origin", "user"),
            })

        return tool_success(result)

    except Exception as e:
        logging.error(f"list_tasks error: {e}", exc_info=True)
        return tool_error(str(e))


async def cancel_task(client=None, source_user=None, *, task_id: str) -> str:
    """
    Cancel a pending scheduled task by ID.

    Args:
        client:      Adapter client.
        source_user: ChatUser — only owns tasks can be cancelled.
        task_id:     The UUID of the task to cancel.

    Returns:
        JSON success/error string.
    """
    try:
        scheduler = _get_scheduler()
        user_id, _, _ = _context_from_source(source_user, client)
        cancelled = await scheduler.cancel_task(task_id, user_id)

        if cancelled:
            return tool_success({"cancelled": task_id})
        return tool_error(
            f"Task {task_id!r} not found or already running/done.",
            task_id=task_id,
        )

    except Exception as e:
        logging.error(f"cancel_task error: {e}", exc_info=True)
        return tool_error(str(e))


async def schedule_self_task(
    client=None,
    source_user=None,
    *,
    prompt: str,
    run_at: str,
    label: str = "",
    recurrence: str = "",
    recurrence_interval: int = 0,
    context_messages: int = 10,
    notify_target: str = "",
    ttl_runs: int = 0,
) -> str:
    """
    Create a self-scheduled task — the AI schedules work for itself.

    Args:
        client:               Adapter client (provides conversation context).
        source_user:          ChatUser with .id and platform info.
        prompt:               What Nami should do when the task fires.
        run_at:               When to run — natural language, ISO datetime, or cron.
        label:                Human-readable description of the task.
        recurrence:           Repeat pattern: 'hourly', 'daily', 'every_n_hours',
                              'every_n_days', or a cron expression.
        recurrence_interval:  N for interval-based patterns.
        context_messages:     How many history messages to include at fire time.
        notify_target:        Where results go: '<adapter>:<conversation_id>' (e.g. 'discord:123'), 'log', or empty.
        ttl_runs:             Auto-cancel after N runs (0 = unlimited).

    Returns:
        JSON success/error string.
    """
    return await _create_task_common(
        prompt=prompt,
        run_at=run_at,
        label=label,
        recurrence=recurrence,
        recurrence_interval=recurrence_interval,
        context_messages=context_messages,
        source_user=source_user,
        client=client,
        origin="ai",
        ttl_runs=ttl_runs if ttl_runs > 0 else None,
        notify_target=notify_target,
        caller_name="schedule_self_task",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _context_from_source(source_user=None, client=None) -> tuple[str, str, str]:
    """
    Derive (user_id, conversation_id, adapter) from tool call context.

    Primary source: pipeline_ctx ContextVar injected by ai_pipeline before
    tool execution — contains the properly scoped user_id (e.g. "discord:123")
    and conversation_id from the current request.

    Falls back to source_user / client attributes when called outside the
    normal pipeline (e.g. tests or legacy paths).

    The adapter is inferred from the user_id prefix (e.g. "discord:123" → adapter="discord").
    """
    # Prefer pipeline context (set by ai_pipeline.run before tool execution)
    ctx = pipeline_ctx.get()
    user_id = ctx.get("user_id", "")
    conversation_id = ctx.get("conversation_id", "")

    # Fallback: build user_id from source_user if pipeline context is missing
    if not user_id and source_user and hasattr(source_user, "id"):
        user_id = f"unknown:{source_user.id}"

    if not user_id:
        user_id = "api:unknown"

    # Fallback: conversation_id from client channel
    if not conversation_id and client and hasattr(client, "channel") and client.channel:
        conversation_id = str(getattr(client.channel, "id", ""))

    # Infer adapter from user_id prefix (generic: any "adapter:id" format)
    adapter = "none"
    if ":" in user_id:
        prefix = user_id.split(":", 1)[0]
        if prefix not in ("api", "unknown"):
            adapter = prefix

    return user_id, conversation_id, adapter


# ─────────────────────────────────────────────────────────────────────────────
# Tool registration
# ─────────────────────────────────────────────────────────────────────────────

def get_tool() -> list[dict]:
    """
    Returns a list of all scheduling tool schemas.

    Note: tool_loader.py calls get_tool() and expects either a single dict
    or a list of dicts. This file exports a list so all four tools are
    registered in one file.
    """
    return [
        {
            "type": "function",
            "safe": False,
            "categories": ["scheduling"],
            "function": {
                "name": "schedule_task",
                "description": (
                    "Schedule a prompt to run at a future time. "
                    "Use this to set reminders, deferred research tasks, follow-ups, "
                    "or anything that should happen later. "
                    "Supports recurrence (hourly, daily, every N hours/days)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "What to do when the task fires. Be specific.",
                        },
                        "run_at": {
                            "type": "string",
                            "description": (
                                "When to run. Natural language, ISO datetime, or 5-field cron: "
                                "'in 30 minutes', 'tomorrow at 9am', '2026-04-01T10:00', "
                                "'0 9 * * 1-5' (weekdays at 9am, sets recurrence automatically)."
                            ),
                        },
                        "label": {
                            "type": "string",
                            "description": "Optional short name for easier identification.",
                        },
                        "recurrence": {
                            "type": "string",
                            "description": (
                                "Repeat pattern. Must be one of: 'hourly', 'daily', "
                                "'every_n_hours', 'every_n_days', "
                                "or a 5-field cron expression (e.g. '0 9 * * 1-5'). "
                                "Leave empty for a one-shot task. "
                                "If run_at is already a cron expression this is set automatically."
                            ),
                        },
                        "recurrence_interval": {
                            "type": "integer",
                            "description": "N for 'every_n_hours' / 'every_n_days'. Ignored otherwise.",
                        },
                        "context_messages": {
                            "type": "integer",
                            "description": "History messages to include at fire time (default 10).",
                        },
                    },
                    "required": ["prompt", "run_at"],
                },
            },
            "func": schedule_task,
        },
        {
            "type": "function",
            "safe": False,
            "categories": ["scheduling"],
            "function": {
                "name": "schedule_self_task",
                "description": (
                    "Create a self-scheduled task for Nami to run on her own initiative. "
                    "Use this when you notice a pattern (e.g. user always asks about weather at 8am) "
                    "and want to proactively schedule work. "
                    "Self-tasks are labeled origin=ai and capped at 20 total. "
                    "Supports recurrence (hourly, daily, cron) and optional TTL (auto-cancel after N runs)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "What Nami should do when the task fires. Be specific and self-contained.",
                        },
                        "run_at": {
                            "type": "string",
                            "description": (
                                "When to run. Natural language, ISO datetime, or 5-field cron: "
                                "'in 30 minutes', 'tomorrow at 9am', '2026-04-01T10:00', "
                                "'0 9 * * 1-5' (weekdays at 9am, sets recurrence automatically)."
                            ),
                        },
                        "label": {
                            "type": "string",
                            "description": "Human-readable description for the task list.",
                        },
                        "recurrence": {
                            "type": "string",
                            "description": (
                                "Repeat pattern. Must be one of: 'hourly', 'daily', "
                                "'every_n_hours', 'every_n_days', "
                                "or a 5-field cron expression (e.g. '0 9 * * 1-5'). "
                                "Leave empty for a one-shot task."
                            ),
                        },
                        "recurrence_interval": {
                            "type": "integer",
                            "description": "N for 'every_n_hours' / 'every_n_days'. Ignored otherwise.",
                        },
                        "context_messages": {
                            "type": "integer",
                            "description": "History messages to include at fire time (default 10).",
                        },
                        "notify_target": {
                            "type": "string",
                            "description": (
                                "Where to deliver results. One of: "
                                "'<adapter>:<conversation_id>' to send to any connected adapter "
                                "(e.g. 'discord:123456789', 'whatsapp:491234@c.us'), "
                                "'log' to store in DB only, "
                                "or empty to use the current conversation context."
                            ),
                        },
                        "ttl_runs": {
                            "type": "integer",
                            "description": "Max number of runs before auto-cancel (0 = unlimited).",
                        },
                    },
                    "required": ["prompt", "run_at"],
                },
            },
            "func": schedule_self_task,
        },
        {
            "type": "function",
            "safe": True,
            "categories": ["scheduling"],
            "function": {
                "name": "list_tasks",
                "description": "List all pending and running scheduled tasks for the current user.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            "func": list_tasks,
        },
        {
            "type": "function",
            "safe": False,
            "categories": ["scheduling"],
            "function": {
                "name": "cancel_task",
                "description": "Cancel a pending scheduled task by its ID (works for both user and AI tasks).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "The UUID of the task to cancel (from list_tasks).",
                        }
                    },
                    "required": ["task_id"],
                },
            },
            "func": cancel_task,
        },
    ]
