"""
event_bus.py — Internal pub/sub for decoupling services from global_registry.

The EventBus provides a lightweight publish/subscribe channel within the
application. Services publish events (e.g. memory.extracted, system.startup)
and other services subscribe to react — without reaching into global_registry
for each other's instances.

This is additive: global_registry stays for now. The EventBus sits alongside
it as a decoupled communication channel.

Known event types
-----------------
system.startup_complete   — Published once after all services are wired.
system.shutdown           — Published before teardown begins.
system.reload_tools       — Full hot-reload of all tools (ToolContext.for_chat).
system.module_changed     — Selective hot-reload for a single module.
                            data.module_path identifies the module (e.g.
                            ``lib.services.context_builder``). Dispatched by
                            HotReloadRegistry to the registered handler.
                            data.file_path is the absolute path to the changed
                            file (optional, used by single-tool reload).
memory.extracted          — A new memory was extracted from a conversation.
task.completed            — A scheduled task finished execution.
task.due                  — A scheduled task is due for execution.
message.received          — An inbound message arrived via an adapter.
message.send              — A tool/trigger wants to send an outbound message.
response.ready            — AI response is ready to be sent to adapters.
activity.recorded         — User interaction occurred (resets idle timers).
"""

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from collections.abc import Callable, Awaitable


@dataclass
class Event:
    """A named event with arbitrary data payload."""

    type: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """
    Lightweight async publish/subscribe with optional wait_for.

    Subscribers are async callables that receive an Event.  publish() fans
    out to all matching subscribers concurrently (gather with return_exceptions).

    wait_for() lets a coroutine block until a specific event type fires
    (or timeout expires), useful for tests and synchronization.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[Event], Awaitable[None]]]] = {}
        self._waiters: dict[str, list[asyncio.Future]] = {}

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(
        self, event_type: str, handler: Callable[[Event], Awaitable[None]]
    ) -> None:
        """Register an async handler for *event_type*."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def unsubscribe(
        self, event_type: str, handler: Callable[[Event], Awaitable[None]]
    ) -> None:
        """Remove a previously registered handler."""
        subs = self._subscribers.get(event_type, [])
        if handler in subs:
            subs.remove(handler)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """Fan out *event* to every subscriber and resolve any waiters.

        Handlers may be either ``async def`` coroutine functions or plain
        synchronous callables.  Sync handlers are called directly; only
        awaitables are gathered so that a non-async subscriber never causes
        a ``TypeError`` inside ``asyncio.gather``.
        """
        # Resolve waiters first — they get the event once
        waiters = self._waiters.pop(event.type, [])
        for fut in waiters:
            if not fut.done():
                fut.set_result(event)

        handlers = self._subscribers.get(event.type, [])
        if not handlers:
            return

        awaitables = []
        awaitable_handlers = []
        for h in handlers:
            try:
                result = h(event)
                if inspect.isawaitable(result):
                    awaitables.append(result)
                    awaitable_handlers.append(h)
                # Sync handlers: result already consumed, nothing more to do.
            except Exception as exc:
                logging.error(
                    f"[event_bus] Subscriber {getattr(h, '__name__', repr(h))!r} raised on "
                    f"{event.type!r}: {exc}",
                    exc_info=exc,
                )

        if awaitables:
            results = await asyncio.gather(*awaitables, return_exceptions=True)
            for h, result in zip(awaitable_handlers, results):
                if isinstance(result, Exception):
                    logging.error(
                        f"[event_bus] Subscriber {getattr(h, '__name__', repr(h))!r} raised on "
                        f"{event.type!r}: {result}",
                        exc_info=result,
                    )

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------

    async def wait_for(self, event_type: str, timeout: float) -> Event:
        """Block until *event_type* is published or *timeout* expires.

        Raises asyncio.TimeoutError on timeout.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._waiters.setdefault(event_type, []).append(fut)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            # Clean up the future so it doesn't leak
            waiters = self._waiters.get(event_type, [])
            if fut in waiters:
                waiters.remove(fut)
            raise

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def subscriber_count(self) -> dict[str, int]:
        """Return {event_type: handler_count} for diagnostics."""
        return {k: len(v) for k, v in self._subscribers.items()}
