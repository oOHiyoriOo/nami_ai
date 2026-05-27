"""
Tests for lib/services/memory_processor.py

Covers:
- _has_required_fields: each memory type (EpisodicMemory, KnowledgeUnit, ProceduralUnit)
- _memory_text: correct field extracted per type
- _is_duplicate: similarity threshold logic with mocked memory_db
- process_memories: full flow with mocked extractor + db (stores only valid, non-duplicate)
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.services.memory_processor import (
    _has_required_fields,
    _memory_text,
    _is_duplicate,
    process_memories,
)


# ---------------------------------------------------------------------------
# _has_required_fields — pure function
# ---------------------------------------------------------------------------

class FakeMemory:
    def __init__(self, memory_type, memory_args):
        self.memory_type = memory_type
        self.memory_args = memory_args
    def is_valid(self): return True


def test_episodic_valid():
    m = FakeMemory("EpisodicMemory", {"summary": "User went hiking"})
    assert _has_required_fields(m)


def test_episodic_missing_summary():
    m = FakeMemory("EpisodicMemory", {"other": "data"})
    assert not (_has_required_fields(m))


def test_knowledge_valid():
    m = FakeMemory("KnowledgeUnit", {"statement": "Python is a language"})
    assert _has_required_fields(m)


def test_knowledge_missing_statement():
    m = FakeMemory("KnowledgeUnit", {})
    assert not (_has_required_fields(m))


def test_procedural_valid():
    m = FakeMemory("ProceduralUnit", {"description": "deploy procedure"})
    assert _has_required_fields(m)


def test_procedural_missing_description():
    m = FakeMemory("ProceduralUnit", {"name": "something"})
    # name alone is NOT sufficient (description is required)
    assert not (_has_required_fields(m))


# ---------------------------------------------------------------------------
# _memory_text — pure function
# ---------------------------------------------------------------------------

def test_memory_text_episodic():
    m = FakeMemory("EpisodicMemory", {"summary": "went hiking"})
    assert _memory_text(m) == "went hiking", f"got: {_memory_text(m)!r}"


def test_memory_text_knowledge():
    m = FakeMemory("KnowledgeUnit", {"statement": "sky is blue"})
    assert _memory_text(m) == "sky is blue", f"got: {_memory_text(m)!r}"


def test_memory_text_procedural_name():
    m = FakeMemory("ProceduralUnit", {"name": "deploy", "description": "run ./deploy.sh"})
    assert _memory_text(m) == "run ./deploy.sh", f"got: {_memory_text(m)!r}"


def test_memory_text_unknown_type():
    m = FakeMemory("Alien", {"data": "xyz"})
    assert _memory_text(m) == "", f"got: {_memory_text(m)!r}"


# ---------------------------------------------------------------------------
# _is_duplicate — mocked memory_db
# ---------------------------------------------------------------------------

def test_is_duplicate_above_threshold():
    """Similarity score >= 0.95 → is duplicate."""

    memory_db = MagicMock()
    memory_db.search = AsyncMock(return_value=[["mem_id", 0.97]])
    m = FakeMemory("KnowledgeUnit", {"statement": "Python is great"})

    result = asyncio.run(_is_duplicate(memory_db, "user1", m, threshold=0.95))
    assert result


def test_is_duplicate_below_threshold():
    """Similarity score < 0.95 → not a duplicate."""

    memory_db = MagicMock()
    memory_db.search = AsyncMock(return_value=[["mem_id", 0.72]])
    m = FakeMemory("KnowledgeUnit", {"statement": "different idea"})

    result = asyncio.run(_is_duplicate(memory_db, "user1", m, threshold=0.95))
    assert not (result)


def test_is_duplicate_empty_results():
    """No search results → not a duplicate."""

    memory_db = MagicMock()
    memory_db.search = AsyncMock(return_value=[])
    m = FakeMemory("EpisodicMemory", {"summary": "brand new memory"})

    result = asyncio.run(_is_duplicate(memory_db, "user1", m))
    assert not (result)


# ---------------------------------------------------------------------------
# process_memories — full flow with mocks
# ---------------------------------------------------------------------------

def test_process_memories_stores_valid():
    """Valid non-duplicate memories are stored."""

    from unittest.mock import patch

    valid_memory = FakeMemory("KnowledgeUnit", {"statement": "The sky is blue"})

    mock_extractor = MagicMock()
    mock_extractor.extract_memories = AsyncMock(return_value=[valid_memory])

    mock_db = MagicMock()
    mock_db.search = AsyncMock(return_value=[])  # no duplicates
    mock_db.add_memory = AsyncMock()

    g_data_patch = {
        "memory_extractor": mock_extractor,
        "memory_db": mock_db,
    }

    async def run():
        with patch("lib.global_registry.g_data") as mock_gd:
            mock_gd.get = lambda key, default=None: g_data_patch.get(key, default)
            await process_memories(
                message_content="User: The sky is blue — I've always found this fascinating because it relates to Rayleigh scattering, where shorter wavelengths of light are scattered more by the atmosphere.\nAssistant: Yes, you are absolutely right. The blue color of the sky is a direct result of this optical phenomenon discovered by Lord Rayleigh in the 19th century.",
                user_id="discord:123",
                user_name="TestUser",
                conversation_id="chan:456",
            )

    asyncio.run(run())
    mock_db.add_memory.assert_called_once()


def test_process_memories_skips_duplicate():
    """Duplicate memories are not stored."""

    from unittest.mock import patch

    dup_memory = FakeMemory("KnowledgeUnit", {"statement": "already known"})

    mock_extractor = MagicMock()
    mock_extractor.extract_memories = AsyncMock(return_value=[dup_memory])

    mock_db = MagicMock()
    mock_db.search = AsyncMock(return_value=[["mem_id", 0.99]])  # high similarity = duplicate
    mock_db.add_memory = AsyncMock()

    g_data_patch = {
        "memory_extractor": mock_extractor,
        "memory_db": mock_db,
    }

    async def run():
        with patch("lib.global_registry.g_data") as mock_gd:
            mock_gd.get = lambda key, default=None: g_data_patch.get(key, default)
            await process_memories(
                message_content="User: already known.\nAssistant: Yes.",
                user_id="discord:123",
                user_name="TestUser",
                conversation_id="chan:456",
            )

    asyncio.run(run())
    mock_db.add_memory.assert_not_called()


def test_process_memories_skips_invalid():
    """Invalid memories (missing required fields) are not stored."""

    from unittest.mock import patch

    bad_memory = FakeMemory("EpisodicMemory", {})  # no 'summary' field

    mock_extractor = MagicMock()
    mock_extractor.extract_memories = AsyncMock(return_value=[bad_memory])

    mock_db = MagicMock()
    mock_db.add_memory = AsyncMock()

    g_data_patch = {
        "memory_extractor": mock_extractor,
        "memory_db": mock_db,
    }

    async def run():
        with patch("lib.global_registry.g_data") as mock_gd:
            mock_gd.get = lambda key, default=None: g_data_patch.get(key, default)
            await process_memories(
                message_content="something",
                user_id="discord:123",
                user_name="TestUser",
                conversation_id="chan:456",
            )

    asyncio.run(run())
    mock_db.add_memory.assert_not_called()


def test_process_memories_no_services_exits_silently():
    """Missing extractor or db → returns without error."""

    from unittest.mock import patch

    async def run():
        with patch("lib.global_registry.g_data") as mock_gd:
            mock_gd.get = lambda key, default=None: None
            await process_memories("content", "user", "name", "conv")

    try:
        asyncio.run(run())
    except Exception as e:
        assert False, f"raised: {e}"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))