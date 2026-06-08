"""
Tests for lib/services/event_bus.py

Covers:
- Event creation and defaults
- subscribe / unsubscribe (including edge cases: nonexistent handler,
  no subscribers for type, double-unsubscribe)
- publish (fan-out to all subscribers, no subscribers)
- publish error handling (exceptions logged, not propagated)
- wait_for (success, timeout, multiple waiters)
- subscriber_count introspection
"""

import asyncio
import sys

import pytest
from pathlib import Path

# Import event_bus directly to avoid triggering the heavy lib.services.__init__
# chain (which pulls in app_initializer → memory_db → sentence_transformers)
_svcs_dir = Path(__file__).parent.parent / "lib" / "services"
sys.path.insert(0, str(_svcs_dir))

from event_bus import Event, EventBus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_event_defaults():
    """Event dataclass: defaults are set correctly."""
    e = Event(type="test.evt")
    if e.type != "test.evt":
        assert False, f"type={e.type!r}"
    if e.data != {}:
        assert False, f"data={e.data!r}"
    assert isinstance(e.timestamp, float) or e.timestamp <= 0, f"timestamp={e.timestamp!r}"


def test_event_with_data():
    """Event dataclass: data kwarg is stored."""
    e = Event(type="mem.extracted", data={"count": 5, "user": "alice"})
    assert e.data == {"count": 5, "user": "alice"}, f"data={e.data!r}"


# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------

def test_subscribe_and_publish():
    """Basic pub/sub: one subscriber receives the event."""
    bus = EventBus()
    received = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe("test.event", handler)

    async def run():
        await bus.publish(Event(type="test.event", data={"x": 1}))

    asyncio.run(run())
    if len(received) != 1:
        assert False, f"expected 1, got {len(received)}"
    assert received[0].data == {"x": 1}, f"data={received[0].data!r}"


def test_multiple_subscribers():
    """Fan-out: all subscribers receive the event."""
    bus = EventBus()
    received = []

    async def h1(event): received.append(("h1", event))
    async def h2(event): received.append(("h2", event))
    async def h3(event): received.append(("h3", event))

    for h in (h1, h2, h3):
        bus.subscribe("ev", h)

    async def run():
        await bus.publish(Event(type="ev", data={}))

    asyncio.run(run())
    if len(received) != 3:
        assert False, f"expected 3, got {len(received)}"
    names = {r[0] for r in received}
    assert names == {"h1", "h2", "h3"}, f"names={names}"


def test_unsubscribe():
    """Unsubscribed handler does NOT receive events."""
    bus = EventBus()
    received = []

    async def h(event): received.append(event)

    bus.subscribe("ev", h)
    bus.unsubscribe("ev", h)

    async def run():
        await bus.publish(Event(type="ev", data={}))

    asyncio.run(run())
    assert not (received), f"received={received}"


def test_unsubscribe_nonexistent_handler():
    """Unsubscribing a handler that was never subscribed is a silent no-op."""
    bus = EventBus()

    async def existing(event): pass
    async def never_added(event): pass

    bus.subscribe("ev", existing)
    # never_added was never subscribed — should not raise
    bus.unsubscribe("ev", never_added)

    # existing handler still intact
    async def run():
        await bus.publish(Event(type="ev", data={}))

    asyncio.run(run())  # should not raise
    assert bus.subscriber_count.get("ev") == 1, (
        f"expected 1 remaining subscriber, got {bus.subscriber_count}"
    )


def test_unsubscribe_no_subscribers_for_type():
    """Unsubscribing from an event type with no subscribers is a silent no-op."""
    bus = EventBus()

    async def handler(event): pass

    bus.unsubscribe("empty.type", handler)  # should not raise

    assert "empty.type" not in bus.subscriber_count, (
        "empty.type should not appear in subscriber_count"
    )


def test_unsubscribe_same_handler_twice():
    """Unsubscribing the same handler twice is a silent no-op on second call."""
    bus = EventBus()

    async def handler(event): pass

    bus.subscribe("ev", handler)
    bus.unsubscribe("ev", handler)
    bus.unsubscribe("ev", handler)  # second unsubscribe — should not raise

    assert bus.subscriber_count.get("ev") == 0, (
        f"expected 0 subscribers, got {bus.subscriber_count}"
    )


def test_publish_no_subscribers():
    """Publishing to an event with no subscribers is a no-op, not an error."""
    bus = EventBus()

    async def run():
        await bus.publish(Event(type="nobody.listens", data={}))

    asyncio.run(run())  # should not raise


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_subscriber_error_does_not_block_others():
    """When one subscriber raises, others still receive the event."""
    bus = EventBus()
    received = []

    async def good(event): received.append("good")
    async def bad(event): raise RuntimeError("boom")

    bus.subscribe("ev", bad)
    bus.subscribe("ev", good)

    async def run():
        await bus.publish(Event(type="ev", data={}))

    asyncio.run(run())
    assert "good" in received, f"good handler not called; received={received}"


# ---------------------------------------------------------------------------
# wait_for
# ---------------------------------------------------------------------------

def test_wait_for_receives_event():
    """wait_for returns the published event."""
    bus = EventBus()

    async def run():
        async def publish_later():
            await asyncio.sleep(0.05)
            await bus.publish(Event(type="slow", data={"ok": True}))

        asyncio.create_task(publish_later())
        ev = await bus.wait_for("slow", timeout=1.0)
        assert ev.type == "slow", f"type={ev.type!r}"
        assert ev.data == {"ok": True}, f"data={ev.data!r}"

    asyncio.run(run())


def test_wait_for_timeout():
    """wait_for raises TimeoutError when the event never fires."""
    bus = EventBus()

    async def run():
        with pytest.raises(asyncio.TimeoutError):
            await bus.wait_for("never.happens", timeout=0.05)

    asyncio.run(run())


def test_wait_for_multiple_waiters():
    """Multiple waiters on the same event type all receive it."""
    bus = EventBus()
    results = []

    async def waiter(name):
        ev = await bus.wait_for("multi", timeout=2.0)
        results.append((name, ev.data["n"]))
        return ev

    async def run():
        t1 = asyncio.create_task(waiter("w1"))
        t2 = asyncio.create_task(waiter("w2"))
        await asyncio.sleep(0.05)
        await bus.publish(Event(type="multi", data={"n": 42}))
        await asyncio.gather(t1, t2)

    asyncio.run(run())
    if len(results) != 2:
        assert False, f"expected 2 waiters, got {len(results)}"
    assert results == [("w1", 42), ("w2", 42)], f"{results}"


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------

def test_subscriber_count():
    """subscriber_count reflects subscriptions."""
    bus = EventBus()

    async def h1(event): pass
    async def h2(event): pass

    bus.subscribe("a", h1)
    bus.subscribe("a", h2)
    bus.subscribe("b", h1)

    counts = bus.subscriber_count
    assert counts == {"a": 2, "b": 1}, f"counts={counts}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
