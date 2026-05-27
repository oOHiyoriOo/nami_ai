"""
Tests for OllamaTools/send_message.py — send_message() and get_tool().

Covers:
- Conversation ID → publishes message.send event with type=conversation
- user:<id> prefix → publishes message.send event with type=dm
- Adapter name is lowercased and stripped
- No event_bus available → tool_error
- get_tool() schema structure (adapter param, no platform enum)
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _get_send_message():
    """Import and return the real send_message function."""
    from OllamaTools.send_message import send_message
    return send_message


def _get_get_tool():
    """Import and return the real get_tool function."""
    from OllamaTools.send_message import get_tool
    return get_tool


def _make_event_bus():
    """Return a mock event_bus that tracks published events."""
    bus = MagicMock()
    bus._published = []

    async def _publish(event):
        bus._published.append(event)

    bus.publish = _publish
    return bus


def _patch_gdata(event_bus):
    """Patch g_data.get() to return the given event_bus for the 'event_bus' key."""
    from lib.global_registry import g_data

    def _get(key):
        if key == "event_bus":
            return event_bus
        return g_data._registry.get(key)

    return patch.object(g_data, "get", side_effect=_get)


def asyncio_run(coro):
    """Run coroutine in a way that works inside or outside an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, coro).result()


# ═══════════════════════════════════════════════════════════════════════════
# Conversation routing (no user: prefix)
# ═══════════════════════════════════════════════════════════════════════════

def test_conversation_routes_to_send_conversation():
    """Recipient without user: prefix publishes message.send with type=conversation."""
    send_message = _get_send_message()
    bus = _make_event_bus()

    with _patch_gdata(bus):
        result = asyncio_run(send_message("discord", "123456789012345678", "Hello!"))

    data = json.loads(result)
    assert data["success"] is True
    assert data["data"]["adapter"] == "discord"
    assert data["data"]["to"] == "123456789012345678"
    assert data["data"]["type"] == "conversation"

    assert len(bus._published) == 1
    evt = bus._published[0]
    assert evt.type == "message.send"
    assert evt.data["adapter"] == "discord"
    assert evt.data["recipient"] == "123456789012345678"
    assert evt.data["content"] == "Hello!"


def test_whatsapp_conversation_id_routed_via_send_conversation():
    """WhatsApp conversation ID publishes message.send event."""
    send_message = _get_send_message()
    bus = _make_event_bus()

    with _patch_gdata(bus):
        result = asyncio_run(send_message("whatsapp", "4916095356029@c.us", "Hallo!"))

    data = json.loads(result)
    assert data["success"] is True
    evt = bus._published[0]
    assert evt.data["adapter"] == "whatsapp"
    assert evt.data["recipient"] == "4916095356029@c.us"
    assert evt.data["content"] == "Hallo!"


# ═══════════════════════════════════════════════════════════════════════════
# DM routing (user: prefix)
# ═══════════════════════════════════════════════════════════════════════════

def test_user_prefix_routes_to_send_dm():
    """user:<id> recipient publishes message.send event with type=dm."""
    send_message = _get_send_message()
    bus = _make_event_bus()

    with _patch_gdata(bus):
        result = asyncio_run(
            send_message("discord", "user:987654321098", "Private message")
        )

    data = json.loads(result)
    assert data["success"] is True
    assert data["data"]["adapter"] == "discord"
    assert data["data"]["to"] == "user:987654321098"
    assert data["data"]["type"] == "dm"

    evt = bus._published[0]
    assert evt.type == "message.send"
    assert evt.data["recipient"] == "user:987654321098"


def test_user_prefix_numeric_id():
    """DM with numeric-only user ID after user: prefix."""
    send_message = _get_send_message()
    bus = _make_event_bus()

    with _patch_gdata(bus):
        result = asyncio_run(send_message("discord", "user:123456789", "DM test"))

    data = json.loads(result)
    assert data["success"] is True
    assert data["data"]["type"] == "dm"


# ═══════════════════════════════════════════════════════════════════════════
# Adapter name normalisation
# ═══════════════════════════════════════════════════════════════════════════

def test_adapter_name_lowercased_and_stripped():
    """Adapter name is lowercased and whitespace-stripped."""
    send_message = _get_send_message()
    bus = _make_event_bus()

    with _patch_gdata(bus):
        result = asyncio_run(
            send_message("  Discord  ", "123456789", "Case test")
        )

    data = json.loads(result)
    assert data["success"] is True
    assert data["data"]["adapter"] == "discord"
    evt = bus._published[0]
    assert evt.data["adapter"] == "discord"


def test_whatsapp_adapter_normalised():
    """WhatsApp adapter name normalised from mixed case."""
    send_message = _get_send_message()
    bus = _make_event_bus()

    with _patch_gdata(bus):
        result = asyncio_run(send_message("WhatsApp", "4916095356029", "Case test"))

    data = json.loads(result)
    assert data["success"] is True
    assert data["data"]["adapter"] == "whatsapp"


# ═══════════════════════════════════════════════════════════════════════════
# Error handling
# ═══════════════════════════════════════════════════════════════════════════

def test_no_adapter_manager_returns_error():
    """No event_bus in g_data returns tool_error."""
    send_message = _get_send_message()

    with _patch_gdata(None):
        result = asyncio_run(send_message("discord", "123456789", "Test"))

    data = json.loads(result)
    assert data["success"] is False
    assert "event_bus" in data["error"].lower()


def test_send_conversation_failure_returns_error():
    """Tool still publishes event and returns success; downstream adapter handles errors."""
    send_message = _get_send_message()
    bus = _make_event_bus()

    with _patch_gdata(bus):
        result = asyncio_run(send_message("discord", "999999999999", "Ghost channel"))

    # Tool's job is to publish the event — dispatch errors are handled by adapter_manager subscriber
    data = json.loads(result)
    assert data["success"] is True
    assert len(bus._published) == 1


def test_send_dm_failure_returns_error():
    """Tool publishes event and returns success regardless of downstream dispatch."""
    send_message = _get_send_message()
    bus = _make_event_bus()

    with _patch_gdata(bus):
        result = asyncio_run(send_message("discord", "user:000000000", "Blocked user"))

    data = json.loads(result)
    assert data["success"] is True
    assert len(bus._published) == 1


def test_arbitrary_adapter_name_works():
    """Any adapter name is passed through without validation at tool layer."""
    send_message = _get_send_message()
    bus = _make_event_bus()

    with _patch_gdata(bus):
        result = asyncio_run(send_message("telegram", "anyone", "Hello"))

    data = json.loads(result)
    assert data["success"] is True
    evt = bus._published[0]
    assert evt.data["adapter"] == "telegram"
    assert evt.data["recipient"] == "anyone"


# ═══════════════════════════════════════════════════════════════════════════
# get_tool() schema
# ═══════════════════════════════════════════════════════════════════════════

def test_get_tool_returns_schema():
    """get_tool() returns a complete tool schema dict."""
    get_tool = _get_get_tool()
    schema = get_tool()

    assert schema["type"] == "function"
    assert schema["safe"] is False
    assert "communication" in schema["categories"]

    func = schema["function"]
    assert func["name"] == "send_message"

    params = func["parameters"]
    assert params["type"] == "object"
    assert "adapter" in params["properties"]
    assert "recipient" in params["properties"]
    assert "message" in params["properties"]
    assert set(params["required"]) == {"adapter", "recipient", "message"}


def test_get_tool_func_is_callable():
    """get_tool() includes the actual send_message function."""
    get_tool = _get_get_tool()
    schema = get_tool()

    assert "func" in schema
    assert callable(schema["func"])


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
