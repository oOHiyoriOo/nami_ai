"""
task_notification_queue.py — Buffers completed background-task notifications for chat injection.

Any background task (research, scheduled tasks, dreams, etc.) fires a ``task.completed``
event on the EventBus.  This queue collects them in memory and the ContextBuilder drains
them into Nami's context at the start of the next chat turn — so she always knows what
she's been up to between conversations.

Event payload for ``task.completed``:
    {
        "task_type": str,   # e.g. "research", "scheduled", "dream"
        "title":     str,   # human-readable task name / topic
        "summary":   str,   # brief description of what was learned/done (optional)
    }
"""
from __future__ import annotations

import logging
from collections import deque

from lib.services.event_bus import Event


class TaskNotificationQueue:
    """
    In-memory FIFO of completed background tasks not yet surfaced in chat.

    Wired during app startup:
        event_bus.subscribe("task.completed", task_notification_queue.on_task_completed)

    The ContextBuilder calls ``pop_pending()`` once per conversation turn.
    """

    def __init__(self) -> None:
        self._pending: deque[dict] = deque()

    async def on_task_completed(self, event: Event) -> None:
        """EventBus subscriber — enqueues the completed task payload.

        Only queues events that carry a ``task_type`` field.  Plain scheduled-task
        lifecycle events (task_id / result / success) are intentionally excluded —
        they are handled by TaskScheduler and NotificationPipeline already.

        For scheduled tasks specifically, only silent runs (adapter="none") are
        queued here — tasks with an adapter are already delivered to the conversation
        by NotificationPipeline.
        """
        if "task_type" not in event.data:
            return
        # Scheduled tasks with an active adapter are delivered to the conversation
        # directly — no need to surface them again in context.
        if event.data.get("task_type") == "scheduled" and event.data.get("adapter", "none") != "none":
            return
        self._pending.append(event.data)
        logging.debug(
            "[task_notification_queue] queued: %s / %s",
            event.data.get("task_type", "?"),
            event.data.get("title", "?")[:60],
        )

    def pop_pending(self) -> list[dict]:
        """Drain and return all pending notifications, clearing the queue."""
        result = list(self._pending)
        self._pending.clear()
        return result
