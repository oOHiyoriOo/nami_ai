"""Tests for MemoryHierarchy."""
import importlib.util
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _load_module():
    """Load memory_hierarchy module directly, bypassing lib.services.__init__."""
    filepath = Path(__file__).parent.parent / "lib" / "services" / "memory_hierarchy.py"
    spec = importlib.util.spec_from_file_location("memory_hierarchy", filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mh = _load_module()
MemoryEntry = _mh.MemoryEntry
MemoryHierarchy = _mh.MemoryHierarchy


_SENTINEL = object()


def _make_hierarchy(memory_db=_SENTINEL, similarity_threshold=0.65):
    """Create a MemoryHierarchy with a mocked long_term_memory.
    Pass None explicitly for no long-term memory. Default creates MagicMock."""
    if memory_db is _SENTINEL:
        memory_db = MagicMock()
    return MemoryHierarchy(
        memory_db=memory_db,
        similarity_threshold=similarity_threshold,
    )


# ============================================================
# MemoryEntry tests
# ============================================================


def test_memory_entry_creation():
    entry = MemoryEntry(content="hello", memory_type="episodic", user_id="u1")
    assert entry.content == "hello"
    assert entry.memory_type == "episodic"
    assert entry.user_id == "u1"
    assert entry.score == 0.0
    assert entry.importance == 0.5
    assert entry.access_count == 0
    assert entry.memory_id is None


def test_memory_entry_to_dict():
    now = time.time()
    entry = MemoryEntry(
        content="test", memory_type="knowledge", score=0.8,
        timestamp=now, access_count=3, importance=0.7,
        user_id="u1", memory_id="m123"
    )
    d = entry.to_dict()
    assert d["text"] == "test"
    assert d["type"] == "knowledge"
    assert d["score"] == 0.8
    assert d["timestamp"] == now
    assert d["access_count"] == 3
    assert d["importance"] == 0.7
    assert d["user_id"] == "u1"
    assert d["memory_id"] == "m123"


def test_memory_entry_default_timestamp():
    """Timestamp defaults to current time."""
    before = time.time()
    entry = MemoryEntry(content="x", memory_type="episodic")
    after = time.time()
    assert before <= entry.timestamp <= after


# ============================================================
# MemoryHierarchy._extract_memory_text tests
# ============================================================


class _DummySummary:
    summary = "dummy summary"


class _DummyStatement:
    statement = "dummy statement"


class _DummyDescription:
    description = "dummy description"


class _DummyEmpty:
    pass


def test_extract_text_from_dict_with_summary():
    hier = _make_hierarchy()
    obj = {"summary": "hello world"}
    assert hier._extract_memory_text(obj) == "hello world"


def test_extract_text_from_dict_with_statement():
    hier = _make_hierarchy()
    obj = {"statement": "I am a statement"}
    assert hier._extract_memory_text(obj) == "I am a statement"


def test_extract_text_from_dict_with_description():
    hier = _make_hierarchy()
    obj = {"description": "a description"}
    assert hier._extract_memory_text(obj) == "a description"


def test_extract_text_from_dict_fallback():
    """Empty dict falls through or-sum to str()."""
    hier = _make_hierarchy()
    assert hier._extract_memory_text({}) == "{}"


def test_extract_text_from_object_with_summary():
    hier = _make_hierarchy()
    assert hier._extract_memory_text(_DummySummary()) == "dummy summary"


def test_extract_text_from_object_with_statement():
    hier = _make_hierarchy()
    assert hier._extract_memory_text(_DummyStatement()) == "dummy statement"


def test_extract_text_from_object_with_description():
    hier = _make_hierarchy()
    assert hier._extract_memory_text(_DummyDescription()) == "dummy description"


def test_extract_text_from_object_fallback():
    hier = _make_hierarchy()
    result = hier._extract_memory_text(_DummyEmpty())
    assert "DummyEmpty" in result


# ============================================================
# MemoryHierarchy.retrieve_memories tests
# ============================================================


@pytest.mark.asyncio
async def test_retrieve_from_long_term():
    """Long-term DB is queried and results are returned."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=5)
    db.search_with_context = AsyncMock(return_value=[
        {
            "memory": {"summary": "lt mem", "id": "m1"},
            "type": "vector",
            "score": 0.88,
        }
    ])
    db.get_driver = MagicMock()

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)

    result = await hier.retrieve_memories(
        query="miss", user_id="u1", top_k=5
    )
    lt_results = [r for r in result if r["tier"] == "long_term"]
    assert len(lt_results) == 1
    assert lt_results[0]["text"] == "lt mem"


@pytest.mark.asyncio
async def test_retrieve_long_term_error_returns_empty():
    """When long-term DB errors, it's caught and returns empty list."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=5)
    db.search_with_context = AsyncMock(side_effect=RuntimeError("DB down"))

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)

    result = await hier.retrieve_memories(query="q", user_id="u1", top_k=5)
    assert result == []


@pytest.mark.asyncio
async def test_retrieve_similarity_threshold_filter():
    """Memories below threshold are filtered out."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=3)
    db.search_with_context = AsyncMock(return_value=[
        {"memory": {"summary": "low score"}, "type": "context", "score": 0.3}
    ])
    db.get_driver = MagicMock()

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)

    result = await hier.retrieve_memories(query="q", user_id="u1", top_k=5)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_retrieve_top_k_limit():
    """Only top_k results are returned."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=10)
    db.search_with_context = AsyncMock(return_value=[
        {"memory": {"summary": f"lt{i}"}, "type": "context", "score": 0.7 + i * 0.01}
        for i in range(10)
    ])
    db.get_driver = MagicMock()

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.0)
    result = await hier.retrieve_memories(query="q", user_id="u1", top_k=3)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_retrieve_empty_hierarchy():
    """Empty hierarchy edge case: no long-term entries."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=0)

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)
    result = await hier.retrieve_memories(query="q", user_id="u1", top_k=5)
    assert result == []


@pytest.mark.asyncio
async def test_retrieve_multiple_long_term_results():
    """Multiple long-term results all come back as tier='long_term'."""
    db = MagicMock()
    db.get_total_entries = AsyncMock(return_value=3)
    db.search_with_context = AsyncMock(return_value=[
        {"memory": {"summary": "lt mem 1"}, "type": "context", "score": 0.9},
        {"memory": {"summary": "lt mem 2"}, "type": "vector", "score": 0.85},
    ])
    db.get_driver = MagicMock()

    hier = MemoryHierarchy(memory_db=db, similarity_threshold=0.65)

    result = await hier.retrieve_memories(query="q", user_id="u1", top_k=5)
    tiers = [r["tier"] for r in result]
    assert all(t == "long_term" for t in tiers)
    assert len(result) == 2


# ============================================================
# MemoryHierarchy.get_stats tests
# ============================================================


@pytest.mark.asyncio
async def test_get_stats_empty():
    """Stats with no DB entries."""
    db = MagicMock()
    mock_result = MagicMock()
    mock_result.single = AsyncMock(return_value=None)
    mock_session = MagicMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    db.get_driver.return_value.session.return_value = mock_session

    hier = MemoryHierarchy(memory_db=db)
    stats = await hier.get_stats()
    assert stats["long_term_total"] == 0


@pytest.mark.asyncio
async def test_get_stats_with_long_term():
    """Stats reflect long-term total."""
    db = MagicMock()
    mock_record = MagicMock()
    mock_record.__getitem__.return_value = 42
    mock_result = MagicMock()
    mock_result.single = AsyncMock(return_value=mock_record)
    mock_session = MagicMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    db.get_driver.return_value.session.return_value = mock_session

    hier = MemoryHierarchy(memory_db=db)
    stats = await hier.get_stats()
    assert stats["long_term_total"] == 42


@pytest.mark.asyncio
async def test_get_stats_with_user_id():
    """Stats with user_id filter."""
    db = MagicMock()
    mock_record = MagicMock()
    mock_record.__getitem__.return_value = 5
    mock_result = MagicMock()
    mock_result.single = AsyncMock(return_value=mock_record)
    mock_session = MagicMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    db.get_driver.return_value.session.return_value = mock_session

    hier = MemoryHierarchy(memory_db=db)
    stats = await hier.get_stats(user_id="u1")
    assert stats["long_term_total"] == 5


@pytest.mark.asyncio
async def test_get_stats_db_error():
    """Stats gracefully handles DB errors."""
    db = MagicMock()
    db.get_driver.side_effect = RuntimeError("DB down")

    hier = MemoryHierarchy(memory_db=db)
    stats = await hier.get_stats()
    assert stats["long_term_total"] == 0


@pytest.mark.asyncio
async def test_get_stats_null_long_term_memory():
    """Stats with no long_term_memory set."""
    hier = _make_hierarchy(memory_db=None)
    stats = await hier.get_stats()
    assert stats["long_term_total"] == 0


# ============================================================
# MemoryHierarchy._increment_access_counts tests
# ============================================================


@pytest.mark.asyncio
async def test_increment_access_counts_success():
    """Increment access counts runs queries for each memory."""
    db = MagicMock()
    mock_session = MagicMock()
    mock_session.run = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    db.get_driver.return_value.session.return_value = mock_session

    hier = MemoryHierarchy(memory_db=db)
    await hier._increment_access_counts([
        ("m1", "EpisodicMemory"),
        ("m2", "KnowledgeUnit"),
    ])
    assert mock_session.run.call_count == 2


@pytest.mark.asyncio
async def test_increment_access_counts_error_handled():
    """Error in access count update is caught and logged."""
    db = MagicMock()
    db.get_driver.side_effect = RuntimeError("DB down")

    hier = MemoryHierarchy(memory_db=db)
    # Should not raise
    await hier._increment_access_counts([("m1", "EpisodicMemory")])
