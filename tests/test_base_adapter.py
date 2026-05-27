"""
Tests for lib/chat_adapters/base_adapter.py — BaseChatAdapter concrete methods

Covers:
- register_message_handler / unregister_message_handler
- _dispatch_message (normal dispatch + exception isolation)
- get_adapter_name
"""

import asyncio
import sys
import importlib.util
from pathlib import Path

import pytest

_project_root = Path(__file__).parent.parent

# Load types.py directly to avoid __init__.py (which triggers Discord import)
_saved_ba_types = sys.modules.get("chat_adapters_types")
_saved_lib_types = sys.modules.get("lib.chat_adapters.types")
_saved_lib_ba = sys.modules.get("lib.chat_adapters.base_adapter")
try:
    _types_path = _project_root / "lib" / "chat_adapters" / "types.py"
    _types_spec = importlib.util.spec_from_file_location("chat_adapters_types", _types_path)
    _types_module = importlib.util.module_from_spec(_types_spec)
    sys.modules["chat_adapters_types"] = _types_module
    _types_spec.loader.exec_module(_types_module)

    # Load base_adapter.py directly, using the already-loaded types module
    sys.modules["lib.chat_adapters.types"] = _types_module
    _base_path = _project_root / "lib" / "chat_adapters" / "base_adapter.py"
    _base_spec = importlib.util.spec_from_file_location("lib.chat_adapters.base_adapter", _base_path)
    _base_module = importlib.util.module_from_spec(_base_spec)
    sys.modules["lib.chat_adapters.base_adapter"] = _base_module
    _base_spec.loader.exec_module(_base_module)

    BaseChatAdapter = _base_module.BaseChatAdapter
    ChatMessage = _types_module.ChatMessage
    ChatUser = _types_module.ChatUser
    ChatChannel = _types_module.ChatChannel
finally:
    for _key, _saved in [
        ("chat_adapters_types", _saved_ba_types),
        ("lib.chat_adapters.types", _saved_lib_types),
        ("lib.chat_adapters.base_adapter", _saved_lib_ba),
    ]:
        if _saved is None:
            sys.modules.pop(_key, None)
        else:
            sys.modules[_key] = _saved


class _TestAdapter(BaseChatAdapter):
    """Minimal concrete adapter for testing BaseChatAdapter concrete methods."""

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    def get_bot_user(self) -> ChatUser:
        return ChatUser(id="bot", name="Bot")
    def get_bot_id(self) -> str: return "bot"
    async def send_message(self, channel, content, reply_to=None, **kwargs):
        return ChatMessage(id="m", content=content, author=self.get_bot_user(),
                           channel=channel, timestamp=__import__("datetime").datetime.now())
    async def send_response(self, response):
        return ChatMessage(id="r", content=response.content, author=self.get_bot_user(),
                           channel=ChatChannel(id="c", name="test"),
                           timestamp=__import__("datetime").datetime.now())
    async def edit_message(self, message, new_content):
        return ChatMessage(id=message.id, content=new_content, author=message.author,
                           channel=message.channel, timestamp=message.timestamp)
    async def get_message_by_id(self, channel, message_id): return None
    async def typing(self, channel):
        class _Ctx:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
        yield _Ctx()
    async def create_thread(self, message, name):
        return ChatChannel(id="thread", name=name)
    async def send_to_thread(self, thread, content):
        return ChatMessage(id="t", content=content, author=self.get_bot_user(),
                           channel=thread, timestamp=__import__("datetime").datetime.now())
    async def set_status(self, status): pass
    def should_respond(self, message): return True
    async def convert_to_chat_message(self, raw_message):
        return ChatMessage(id="raw", content=str(raw_message), author=self.get_bot_user(),
                           channel=ChatChannel(id="c", name="test"),
                           timestamp=__import__("datetime").datetime.now())
    def is_permitted_user(self, user): return True
    def is_ai_channel(self, channel): return True


def _make_msg(content: str = "hello") -> ChatMessage:
    return ChatMessage(
        id="msg-1", content=content,
        author=ChatUser(id="u1", name="User"),
        channel=ChatChannel(id="ch1", name="general"),
        timestamp=__import__("datetime").datetime.now(),
    )


# ==================== Handler Registration ====================

@pytest.mark.asyncio
async def test_register_handler():
    """register_message_handler appends callable to _message_handlers list."""
    adapter = _TestAdapter({})
    received = []

    async def handler(msg: ChatMessage) -> None:
        received.append(msg.content)

    adapter.register_message_handler(handler)
    assert len(adapter._message_handlers) == 1, f"Expected 1 handler, got {len(adapter._message_handlers)}"
    assert adapter._message_handlers[0] is handler, "Handler not properly registered"


@pytest.mark.asyncio
async def test_unregister_handler():
    """unregister_message_handler removes existing handler."""
    adapter = _TestAdapter({})

    async def handler(msg: ChatMessage) -> None: pass

    adapter.register_message_handler(handler)
    adapter.unregister_message_handler(handler)
    assert handler not in adapter._message_handlers, f"Handler still in list: {handler in adapter._message_handlers}"
    assert len(adapter._message_handlers) == 0, f"Expected 0 handlers, got {len(adapter._message_handlers)}"


@pytest.mark.asyncio
async def test_unregister_nonexistent():
    """unregister_message_handler is a no-op when handler not in list (no crash)."""
    adapter = _TestAdapter({})

    async def h1(msg: ChatMessage) -> None: pass
    async def h2(msg: ChatMessage) -> None: pass

    adapter.register_message_handler(h1)
    adapter.unregister_message_handler(h2)  # h2 never registered
    assert len(adapter._message_handlers) == 1, f"Expected h1 still registered, got {adapter._message_handlers}"
    assert adapter._message_handlers[0] is h1, "Wrong handler in list"


# ==================== Message Dispatch ====================

@pytest.mark.asyncio
async def test_dispatch_calls_all_handlers():
    """_dispatch_message calls all registered handlers with the message."""
    adapter = _TestAdapter({})
    received = []

    async def h1(msg: ChatMessage) -> None:
        received.append(f"h1:{msg.content}")

    async def h2(msg: ChatMessage) -> None:
        received.append(f"h2:{msg.content}")

    async def h3(msg: ChatMessage) -> None:
        received.append(f"h3:{msg.content}")

    adapter.register_message_handler(h1)
    adapter.register_message_handler(h2)
    adapter.register_message_handler(h3)

    msg = _make_msg("dispatch test")
    await adapter._dispatch_message(msg)

    expected = ["h1:dispatch test", "h2:dispatch test", "h3:dispatch test"]
    assert received == expected, f"Expected {expected}, got {received}"


@pytest.mark.asyncio
async def test_dispatch_isolates_exceptions():
    """_dispatch_message continues calling remaining handlers if one raises."""
    adapter = _TestAdapter({})
    received = []

    async def h1(msg: ChatMessage) -> None:
        raise RuntimeError("handler explosion")

    async def h2(msg: ChatMessage) -> None:
        received.append("h2 ran")

    async def h3(msg: ChatMessage) -> None:
        received.append("h3 ran")

    adapter.register_message_handler(h1)
    adapter.register_message_handler(h2)
    adapter.register_message_handler(h3)

    msg = _make_msg("error test")
    await adapter._dispatch_message(msg)

    expected = ["h2 ran", "h3 ran"]
    assert received == expected, f"Expected {expected}, got {received}"


# ==================== Adapter Name ====================

def test_get_adapter_name():
    """get_adapter_name returns lowercased class name without 'Adapter' suffix."""
    adapter = _TestAdapter({})
    name = adapter.get_adapter_name()
    assert name == "_test", f"Expected '_test', got '{name}'"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
