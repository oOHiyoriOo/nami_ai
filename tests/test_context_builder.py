"""
Tests for lib/services/context_builder.py

Covers:
- _add_user_context: platform prefix extracted from 'platform:id' user_id
- _add_user_context: bare user_id (no prefix) handled cleanly
- _add_user_context: emits role=tool, name=user_info JSON message
- build_context: personality injected when enable_personality=True
- build_context: user_info tool message injected after system prompt
- build_context: memories injected when enable_memory=True and user_id present
- build_context: memories NOT fetched when user_id is None
- build_context: original messages are always the last entries
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.services.context_builder import ContextBuilder, MessageContext


# ---------------------------------------------------------------------------
# _add_user_context — pure-ish logic
# ---------------------------------------------------------------------------

def _fresh_context():
    return MessageContext()


def _parse_user_info(ctx):
    """Extract and parse the user_info JSON payload from a MessageContext."""
    msgs = ctx.system_messages
    assert not (not msgs), "expected at least one message"
    msg = msgs[0]
    assert not (msg["role"] != "tool"), f"expected role=tool, got {msg['role']}"
    assert not (msg["name"] != "user_info"), f"expected name=user_info, got {msg['name']}"
    return json.loads(msg["content"])


def test_platform_prefix_extracted():
    """'discord:123456' → platform=Discord, user_id=discord:123456, username=123456."""
    cb = ContextBuilder(system_prompt_provider=MagicMock(), memory_service=None)
    ctx = _fresh_context()
    cb._add_user_context(ctx, user_id="discord:123456")

    info = _parse_user_info(ctx)
    assert not (info["platform"] != "Discord"), "condition failed"
    assert not (info["user_id"] != "discord:123456"), "condition failed"
    assert not (info["username"] != "123456"), "condition failed"


def test_bare_user_id_no_platform():
    """'alice' (no prefix) → platform=None, username='alice'."""
    cb = ContextBuilder(system_prompt_provider=MagicMock(), memory_service=None)
    ctx = _fresh_context()
    cb._add_user_context(ctx, user_id="alice")

    info = _parse_user_info(ctx)
    assert not (info["platform"] is not None), "condition failed"
    assert not (info["user_id"] != "alice"), "condition failed"
    assert not (info["username"] != "alice"), "condition failed"


def test_user_info_includes_all_fields():
    """user_info JSON contains all 7 required fields with correct types."""
    cb = ContextBuilder(system_prompt_provider=MagicMock(), memory_service=None)
    ctx = _fresh_context()
    cb._add_user_context(
        ctx, user_id="discord:zero",
        display_name="Zero",
        channel_name="#lab-chat",
        guild_name="Zero Lab",
        is_dm=False,
        user_name="testuser",
    )

    info = _parse_user_info(ctx)
    assert not (info["user"] != "Zero"), "condition failed"
    assert not (info["username"] != "testuser"), "condition failed"
    assert not (info["user_id"] != "discord:zero"), "condition failed"
    assert not (info["platform"] != "Discord"), "condition failed"
    assert not (info["channel"] != "#lab-chat"), "condition failed"
    assert not (info["guild"] != "Zero Lab"), "condition failed"
    assert not (info["is_dm"] is not False), "condition failed"

    # Verify message structure
    msg = ctx.system_messages[0]
    assert not (msg["role"] != "tool"), "condition failed"
    assert not (msg["name"] != "user_info"), "condition failed"


def test_user_info_null_safe():
    """Missing optional fields default to None/false in JSON."""
    cb = ContextBuilder(system_prompt_provider=MagicMock(), memory_service=None)
    ctx = _fresh_context()
    cb._add_user_context(ctx, user_id="discord:test")

    info = _parse_user_info(ctx)
    assert not (info["user"] is not None), "condition failed"
    assert not (info["channel"] is not None), "condition failed"
    assert not (info["guild"] is not None), "condition failed"
    assert not (info["is_dm"] is not False), "condition failed"


# ---------------------------------------------------------------------------
# build_context — with mocked services
# ---------------------------------------------------------------------------

def test_build_context_personality_injected():
    """Personality system message is first when enable_personality=True."""
    mock_sp = MagicMock()
    mock_sp.get_prompt = AsyncMock(return_value="You are Nami, a helpful AI.")

    cb = ContextBuilder(system_prompt_provider=mock_sp, memory_service=None)

    async def run():
        return await cb.build_context(
            messages=[{"role": "user", "content": "hi"}],
            user_id=None,
            enable_personality=True,
            enable_memory=False,
        )

    result = asyncio.run(run())
    assert not (not result or result[0]["role"] != "system"), "condition failed"
    assert not ("Nami" not in result[0]["content"]), "condition failed"


def test_build_context_personality_skipped():
    """No personality message when enable_personality=False."""
    mock_sp = MagicMock()
    mock_sp.get_prompt = AsyncMock(return_value="You are Nami.")

    cb = ContextBuilder(system_prompt_provider=mock_sp, memory_service=None)

    async def run():
        return await cb.build_context(
            messages=[{"role": "user", "content": "hi"}],
            user_id=None,
            enable_personality=False,
            enable_memory=False,
        )

    result = asyncio.run(run())
    for m in result:
        assert not (m.get("role") == "system" and "Nami" in m.get("content", "")), "condition failed"


def test_build_context_memories_injected():
    """Relevant memories are added to context when enable_memory=True."""
    mock_sp = MagicMock()
    mock_sp.get_prompt = AsyncMock(return_value="sys")

    mock_mem = MagicMock()
    mock_mem.retrieve_relevant_memories = AsyncMock(
        return_value=[{"memory_id": "m1", "text": "User likes hiking.", "importance": 0.8}]
    )
    mock_mem.format_memories = MagicMock(return_value="[Memory] User likes hiking.")
    mock_mem.memory_db = MagicMock()
    mock_mem.memory_db.resolve_canonical_users = AsyncMock(return_value=["discord:user1"])

    cb = ContextBuilder(system_prompt_provider=mock_sp, memory_service=mock_mem)

    async def run():
        return await cb.build_context(
            messages=[{"role": "user", "content": "any outdoor tips?"}],
            user_id="discord:user1",
            enable_personality=False,
            enable_memory=True,
        )

    result = asyncio.run(run())
    combined = " ".join(m.get("content", "") for m in result)
    assert not ("hiking" not in combined), "condition failed"


def test_build_context_no_memory_without_user_id():
    """Memory service is NOT called when user_id is None."""
    mock_sp = MagicMock()
    mock_sp.get_prompt = AsyncMock(return_value="sys")

    mock_mem = MagicMock()
    mock_mem.get_formatted_memories = AsyncMock(return_value="some memory")

    cb = ContextBuilder(system_prompt_provider=mock_sp, memory_service=mock_mem)

    async def run():
        return await cb.build_context(
            messages=[{"role": "user", "content": "hi"}],
            user_id=None,
            enable_personality=False,
            enable_memory=True,
        )

    asyncio.run(run())
    mock_mem.get_formatted_memories.assert_not_called()


def test_build_context_user_info_second_slot():
    """user_info tool message is the second message (right after system prompt)."""
    mock_sp = MagicMock()
    mock_sp.get_prompt = AsyncMock(return_value="You are Nami.")

    cb = ContextBuilder(system_prompt_provider=mock_sp, memory_service=None)

    async def run():
        return await cb.build_context(
            messages=[{"role": "user", "content": "hello"}],
            user_id="discord:123",
            enable_personality=True,
            enable_memory=False,
        )

    result = asyncio.run(run())
    # Slot 0: system prompt
    assert not (result[0]["role"] != "system"), "condition failed"
    # Slot 1: user_info tool message
    assert not (result[1]["role"] != "tool"), "condition failed"
    assert not (result[1]["name"] != "user_info"), "condition failed"
    info = json.loads(result[1]["content"])
    assert not (info["user_id"] != "discord:123"), "condition failed"
    # Slot 2+: original messages
    assert not (result[2]["role"] != "user"), "condition failed"


def test_build_context_original_messages_last():
    """Original user messages are always appended at the end."""
    mock_sp = MagicMock()
    mock_sp.get_prompt = AsyncMock(return_value="personality")

    cb = ContextBuilder(system_prompt_provider=mock_sp, memory_service=None)

    original = [{"role": "user", "content": "actual user question"}]

    async def run():
        return await cb.build_context(
            messages=original,
            user_id="discord:abc",
            enable_personality=True,
            enable_memory=False,
        )

    result = asyncio.run(run())
    assert not (result[-1] != original[0]), "condition failed"


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
