"""
Tests for lib/chat_adapters/types.py

Covers:
- ChatAttachment: is_image, is_video, is_audio content type detection
- ChatUser.__post_init__: display_name defaults and overrides
- ChatMessage.conversation_id: channel ID delegation
- ChatChannel: required fields, defaults, guild info, is_dm flag
- ChatResponse: defaults, reply_to reference, attachments, create_thread
- TypingIndicator: active default and override
- MessageType: enum values, uniqueness, default for ChatMessage
"""

import sys
import importlib.util
from pathlib import Path

# Load types.py directly without triggering __init__.py (which imports discord)
_project_root = Path(__file__).parent.parent
_types_path = _project_root / "lib" / "chat_adapters" / "types.py"
_saved_chat_types = sys.modules.get("chat_adapters_types")
try:
    spec = importlib.util.spec_from_file_location("chat_adapters_types", _types_path)
    _types_module = importlib.util.module_from_spec(spec)
    sys.modules["chat_adapters_types"] = _types_module
    spec.loader.exec_module(_types_module)

    ChatAttachment = _types_module.ChatAttachment
    ChatUser = _types_module.ChatUser
    ChatMessage = _types_module.ChatMessage
    ChatChannel = _types_module.ChatChannel
    ChatResponse = _types_module.ChatResponse
    TypingIndicator = _types_module.TypingIndicator
    MessageType = _types_module.MessageType
finally:
    if _saved_chat_types is None:
        sys.modules.pop("chat_adapters_types", None)
    else:
        sys.modules["chat_adapters_types"] = _saved_chat_types


def _make_attachment(content_type: str | None) -> ChatAttachment:
    return ChatAttachment(
        filename="test_file",
        url="https://example.com/test",
        content_type=content_type,
    )


def test_image_png():
    """content_type='image/png' → is_image=True, is_video=False, is_audio=False"""
    att = _make_attachment("image/png")
    ok = att.is_image and not att.is_video and not att.is_audio
    assert ok, f"[FAIL] image/png: is_image={att.is_image}, is_video={att.is_video}, is_audio={att.is_audio}"


def test_video_mp4():
    """content_type='video/mp4' → is_video=True, others False"""
    att = _make_attachment("video/mp4")
    ok = not att.is_image and att.is_video and not att.is_audio
    assert ok, f"[FAIL] video/mp4: is_image={att.is_image}, is_video={att.is_video}, is_audio={att.is_audio}"


def test_audio_mpeg():
    """content_type='audio/mpeg' → is_audio=True, others False"""
    att = _make_attachment("audio/mpeg")
    ok = not att.is_image and not att.is_video and att.is_audio
    assert ok, f"[FAIL] audio/mpeg: is_image={att.is_image}, is_video={att.is_video}, is_audio={att.is_audio}"


def test_content_type_none():
    """content_type=None → all False, no crash"""
    att = _make_attachment(None)
    ok = not att.is_image and not att.is_video and not att.is_audio
    assert ok, f"[FAIL] content_type=None: is_image={att.is_image}, is_video={att.is_video}, is_audio={att.is_audio}"


def test_content_type_empty():
    """content_type='' → all False (empty string matches no prefix)"""
    att = _make_attachment("")
    ok = not att.is_image and not att.is_video and not att.is_audio
    assert ok, f"[FAIL] content_type='': is_image={att.is_image}, is_video={att.is_video}, is_audio={att.is_audio}"


def test_content_type_cross():
    """Sanity: mixed prefixes — image/jpeg → only is_image"""
    att = _make_attachment("image/jpeg")
    ok = att.is_image and not att.is_video and not att.is_audio
    assert ok, f"[FAIL] image/jpeg: is_image={att.is_image}, is_video={att.is_video}, is_audio={att.is_audio}"


def test_content_type_video_webm():
    """Sanity: video/webm → only is_video"""
    att = _make_attachment("video/webm")
    ok = not att.is_image and att.is_video and not att.is_audio
    assert ok, f"[FAIL] video/webm: is_image={att.is_image}, is_video={att.is_video}, is_audio={att.is_audio}"


def test_content_type_audio_wav():
    """Sanity: audio/wav → only is_audio"""
    att = _make_attachment("audio/wav")
    ok = not att.is_image and not att.is_video and att.is_audio
    assert ok, f"[FAIL] audio/wav: is_image={att.is_image}, is_video={att.is_video}, is_audio={att.is_audio}"


# ──────────────────────────────────────────────
# ChatUser.__post_init__ tests
# ──────────────────────────────────────────────

def test_chatuser_display_name_defaults_to_name():
    """ChatUser(id='1', name='Alice') → display_name auto-set to 'Alice'"""
    user = ChatUser(id="1", name="Alice")
    ok = user.display_name == "Alice"
    assert ok, f"[FAIL] ChatUser(name='Alice'): display_name={user.display_name!r}"


def test_chatuser_display_name_equals_name():
    """ChatUser(id='1', name='Alice') → display_name equals name"""
    user = ChatUser(id="1", name="Alice")
    ok = user.display_name == user.name
    assert ok, f"[FAIL] ChatUser: display_name={user.display_name!r}, name={user.name!r}"


def test_chatuser_display_name_explicit_overrides():
    """ChatUser(id='1', name='Alice', display_name='Ali') → display_name is 'Ali'"""
    user = ChatUser(id="1", name="Alice", display_name="Ali")
    ok = user.display_name == "Ali" and user.name == "Alice"
    assert ok, f"[FAIL] ChatUser(display_name='Ali'): display_name={user.display_name!r}, name={user.name!r}"


def test_chatuser_display_name_empty_preserved():
    """ChatUser(id='1', name='Bob', display_name='') → display_name is '' (not None)"""
    user = ChatUser(id="1", name="Bob", display_name="")
    ok = user.display_name == "" and user.name == "Bob"
    assert ok, f"[FAIL] ChatUser(display_name=''): display_name={user.display_name!r}"


# ──────────────────────────────────────────────
# ChatMessage.conversation_id tests
# ──────────────────────────────────────────────

def test_conversation_id_returns_channel_id():
    """conversation_id returns channel.id"""
    channel = ChatChannel(id="ch-123", name="general")
    author = ChatUser(id="u-1", name="Alice")
    msg = ChatMessage(
        id="msg-1", content="hello", author=author, channel=channel,
        timestamp=__import__("datetime").datetime.now(),
    )
    ok = msg.conversation_id == "ch-123"
    assert ok, f"[FAIL] conversation_id={msg.conversation_id!r}, expected 'ch-123'"


def test_conversation_id_same_for_same_channel():
    """Multiple messages in the same channel share the same conversation_id"""
    channel = ChatChannel(id="ch-456", name="general")
    author = ChatUser(id="u-1", name="Alice")
    dt = __import__("datetime").datetime.now()
    msg1 = ChatMessage(id="msg-1", content="hello", author=author, channel=channel, timestamp=dt)
    msg2 = ChatMessage(id="msg-2", content="world", author=author, channel=channel, timestamp=dt)
    ok = msg1.conversation_id == msg2.conversation_id == "ch-456"
    assert ok, f"[FAIL] msg1={msg1.conversation_id!r}, msg2={msg2.conversation_id!r}"


def test_conversation_id_differs_for_diff_channels():
    """Messages in different channels have different conversation_id"""
    author = ChatUser(id="u-1", name="Alice")
    ch1 = ChatChannel(id="ch-1", name="general")
    ch2 = ChatChannel(id="ch-2", name="random")
    dt = __import__("datetime").datetime.now()
    msg1 = ChatMessage(id="msg-1", content="hello", author=author, channel=ch1, timestamp=dt)
    msg2 = ChatMessage(id="msg-2", content="world", author=author, channel=ch2, timestamp=dt)
    ok = msg1.conversation_id == "ch-1" and msg2.conversation_id == "ch-2" and msg1.conversation_id != msg2.conversation_id
    assert ok, f"[FAIL] msg1={msg1.conversation_id!r}, msg2={msg2.conversation_id!r}"


# ──────────────────────────────────────────────
# ChatChannel tests
# ──────────────────────────────────────────────

def test_chat_channel_required_fields():
    """ChatChannel(id='ch-1', name='general') → id and name set correctly"""
    ch = ChatChannel(id="ch-1", name="general")
    ok = ch.id == "ch-1" and ch.name == "general"
    assert ok, f"[FAIL] ChatChannel: id={ch.id!r}, name={ch.name!r}"


def test_chat_channel_defaults():
    """ChatChannel defaults: is_dm=False, guild_id=None, guild_name=None, metadata={}"""
    ch = ChatChannel(id="ch-2", name="random")
    ok = (
        ch.is_dm is False
        and ch.guild_id is None
        and ch.guild_name is None
        and ch.metadata == {}
    )
    assert ok, f"[FAIL] ChatChannel defaults: is_dm={ch.is_dm!r}, guild_id={ch.guild_id!r}, guild_name={ch.guild_name!r}, metadata={ch.metadata!r}"


def test_chat_channel_with_guild():
    """ChatChannel with guild_id and guild_name set"""
    ch = ChatChannel(id="ch-3", name="general", is_dm=False, guild_id="g-1", guild_name="Test Guild")
    ok = ch.guild_id == "g-1" and ch.guild_name == "Test Guild" and ch.is_dm is False
    assert ok, f"[FAIL] ChatChannel guild: guild_id={ch.guild_id!r}, guild_name={ch.guild_name!r}"


def test_chat_channel_is_dm():
    """ChatChannel with is_dm=True"""
    ch = ChatChannel(id="ch-dm", name="DM", is_dm=True)
    ok = ch.is_dm is True
    assert ok, f"[FAIL] ChatChannel is_dm={ch.is_dm!r}"


# ──────────────────────────────────────────────
# ChatResponse tests
# ──────────────────────────────────────────────

def test_chat_response_defaults():
    """ChatResponse defaults: attachments=[], create_thread=False, reply_to=None, thread_name=None, metadata={}"""
    r = ChatResponse(content="hello")
    ok = (
        r.content == "hello"
        and r.attachments == []
        and r.create_thread is False
        and r.reply_to is None
        and r.thread_name is None
        and r.metadata == {}
    )
    assert ok, f"[FAIL] ChatResponse defaults: content={r.content!r}, attachments={r.attachments!r}, create_thread={r.create_thread!r}, reply_to={r.reply_to!r}, thread_name={r.thread_name!r}, metadata={r.metadata!r}"


def test_chat_response_with_reply():
    """ChatResponse with reply_to pointing to a ChatMessage"""
    channel = ChatChannel(id="ch-r", name="general")
    author = ChatUser(id="u-r", name="Bob")
    dt = __import__("datetime").datetime.now()
    msg = ChatMessage(id="msg-r", content="original", author=author, channel=channel, timestamp=dt)
    r = ChatResponse(content="reply", reply_to=msg)
    ok = r.reply_to is msg and r.reply_to.id == "msg-r" and r.content == "reply"
    assert ok, f"[FAIL] ChatResponse reply_to: {r.reply_to!r}"


def test_chat_response_with_attachments():
    """ChatResponse with file path attachments"""
    r = ChatResponse(content="check this", attachments=["/tmp/file1.png", "/tmp/file2.pdf"])
    ok = r.attachments == ["/tmp/file1.png", "/tmp/file2.pdf"]
    assert ok, f"[FAIL] ChatResponse attachments: {r.attachments!r}"


def test_chat_response_create_thread():
    """ChatResponse with create_thread=True and thread_name"""
    r = ChatResponse(content="new thread", create_thread=True, thread_name="My Thread")
    ok = r.create_thread is True and r.thread_name == "My Thread"
    assert ok, f"[FAIL] ChatResponse thread: create_thread={r.create_thread!r}, thread_name={r.thread_name!r}"


# ──────────────────────────────────────────────
# TypingIndicator tests
# ──────────────────────────────────────────────

def test_typing_indicator_defaults():
    """TypingIndicator defaults: active=True"""
    ch = ChatChannel(id="ch-t", name="typing")
    ti = TypingIndicator(channel=ch)
    ok = ti.channel is ch and ti.active is True
    assert ok, f"[FAIL] TypingIndicator: active={ti.active!r}, channel={ti.channel!r}"


def test_typing_indicator_inactive():
    """TypingIndicator with active=False"""
    ch = ChatChannel(id="ch-t2", name="stopped")
    ti = TypingIndicator(channel=ch, active=False)
    ok = ti.active is False
    assert ok, f"[FAIL] TypingIndicator active={ti.active!r}"


# ──────────────────────────────────────────────
# MessageType enum tests
# ──────────────────────────────────────────────

def test_message_type_enum_values():
    """MessageType enum has expected values"""
    ok = (
        MessageType.DEFAULT.value == "default"
        and MessageType.REPLY.value == "reply"
        and MessageType.SYSTEM.value == "system"
        and MessageType.COMMAND.value == "command"
    )
    assert ok, f"[FAIL] MessageType values: DEFAULT={MessageType.DEFAULT.value!r}, REPLY={MessageType.REPLY.value!r}, SYSTEM={MessageType.SYSTEM.value!r}, COMMAND={MessageType.COMMAND.value!r}"


def test_message_type_enum_comparison():
    """MessageType enum members are unique"""
    ok = (
        MessageType.DEFAULT != MessageType.REPLY
        and MessageType.REPLY != MessageType.SYSTEM
        and MessageType.SYSTEM != MessageType.COMMAND
    )
    assert ok, f"[FAIL] MessageType: enum members not distinct"


def test_message_type_default_for_chatmessage():
    """ChatMessage defaults to MessageType.DEFAULT"""
    channel = ChatChannel(id="ch-mt", name="test")
    author = ChatUser(id="u-mt", name="Tester")
    dt = __import__("datetime").datetime.now()
    msg = ChatMessage(id="msg-mt", content="test", author=author, channel=channel, timestamp=dt)
    ok = msg.message_type == MessageType.DEFAULT
    assert ok, f"[FAIL] ChatMessage.message_type={msg.message_type!r}"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))