"""
Tests for OllamaTools/dream_tools.py — Auto-Dream agent memory tools.

Covers:
- get_tool() returns all 7 tool schemas correctly structured
- dream_list_memories with type filtering
- dream_search_memories via semantic query
- dream_get_memory by ID
- dream_update_memory rewrites content
- dream_delete_memory by ID with reason
- dream_merge_memories (update + delete)
- dream_get_stats counts per type
- Null memory_db graceful handling
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from OllamaTools.dream_tools import (
    dream_list_memories,
    dream_search_memories,
    dream_get_memory,
    dream_update_memory,
    dream_delete_memory,
    dream_merge_memories,
    dream_get_stats,
    dream_find_important_memories,
    dream_replay_memory,
    get_tool,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_mem_obj(memory_type: str, mem_id: str, text: str):
    """Create a fake memory object with to_dict() and type info."""
    cls_name = memory_type
    field_map = {
        "EpisodicMemory": "summary",
        "KnowledgeUnit": "statement",
        "ProceduralUnit": "description",
    }
    text_field = field_map.get(memory_type, "summary")
    obj = MagicMock()
    obj.__class__.__name__ = cls_name
    obj.to_dict.return_value = {
        "id": mem_id,
        text_field: text,
        "summaryEmbeddingVector": [0.1, 0.2, 0.3],
        "confidenceScore": 0.9,
    }
    return obj


def _make_mock_db():
    """Create a mock MemoryDb with all needed methods."""
    db = MagicMock()
    db.MEMORY_TYPES = {
        "EpisodicMemory": (MagicMock(), "summary"),
        "KnowledgeUnit": (MagicMock(), "statement"),
        "ProceduralUnit": (MagicMock(), "description"),
    }
    db._encode = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return db


def _make_mock_session(run_return_value):
    """Create a mock neo4j session whose run() returns the given value."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.run = AsyncMock(return_value=run_return_value)
    return session


def _make_mock_driver(session):
    """Create a mock driver whose session() returns the given session."""
    driver = MagicMock()
    driver.session.return_value = session
    return driver


# ── get_tool() tests ─────────────────────────────────────────────────

def test_get_tool_returns_list():
    """get_tool() returns a list."""
    tools = get_tool()
    assert isinstance(tools, list), f"[FAIL] Expected list, got {type(tools)}"


def test_get_tool_has_nine_schemas():
    """get_tool() returns 9 tool definitions."""
    tools = get_tool()
    assert len(tools) == 9, f"[FAIL] Expected 9 tools, got {len(tools)}"


def test_get_tool_names_are_correct():
    """get_tool() schemas have expected dream_ prefixed names."""
    tools = get_tool()
    expected = {
        "dream_get_stats",
        "dream_list_memories",
        "dream_search_memories",
        "dream_get_memory",
        "dream_update_memory",
        "dream_delete_memory",
        "dream_merge_memories",
        "dream_find_important_memories",
        "dream_replay_memory",
    }
    actual = {t["function"]["name"] for t in tools}
    assert actual == expected, f"[FAIL] Expected names {expected}, got {actual}"


def test_get_tool_funcs_are_callable():
    """Each tool schema has a callable 'func' key."""
    tools = get_tool()
    for t in tools:
        name = t["function"]["name"]
        func = t.get("func")
        assert callable(func), f"[FAIL] '{name}' func is not callable: {type(func)}"


def test_get_tool_each_has_type_and_categories():
    """Each tool has 'type', 'safe', and 'categories' keys."""
    tools = get_tool()
    for t in tools:
        name = t["function"]["name"]
        assert "type" in t, f"[FAIL] '{name}' missing 'type'"
        assert "safe" in t, f"[FAIL] '{name}' missing 'safe'"
        assert "categories" in t, f"[FAIL] '{name}' missing 'categories'"


def test_get_tool_safe_flags_are_correct():
    """Read tools are safe=True, write tools are safe=False."""
    tools = get_tool()
    safe_values = {t["function"]["name"]: t.get("safe") for t in tools}
    read_tools = ["dream_get_stats", "dream_list_memories", "dream_search_memories", "dream_get_memory", "dream_find_important_memories"]
    write_tools = ["dream_update_memory", "dream_delete_memory", "dream_merge_memories", "dream_replay_memory"]
    for name in read_tools:
        assert safe_values.get(name) is True, f"[FAIL] '{name}' expected safe=True, got {safe_values.get(name)}"
    for name in write_tools:
        assert safe_values.get(name) is False, f"[FAIL] '{name}' expected safe=False, got {safe_values.get(name)}"


def test_get_tool_categories_are_correct():
    """Read tools are memory_read, write tools are memory_write."""
    tools = get_tool()
    cat_values = {t["function"]["name"]: t.get("categories", []) for t in tools}
    read_tools = ["dream_get_stats", "dream_list_memories", "dream_search_memories", "dream_get_memory", "dream_find_important_memories"]
    write_tools = ["dream_update_memory", "dream_delete_memory", "dream_merge_memories", "dream_replay_memory"]
    for name in read_tools:
        assert "memory_read" in cat_values.get(name, []), f"[FAIL] '{name}' expected memory_read, got {cat_values.get(name)}"
    for name in write_tools:
        assert "memory_write" in cat_values.get(name, []), f"[FAIL] '{name}' expected memory_write, got {cat_values.get(name)}"


# ── dream_list_memories tests ────────────────────────────────────────

def test_dream_list_memories_db_not_available():
    """dream_list_memories returns error when memory_db is None."""
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = None
        result = asyncio.run(dream_list_memories())
        assert "Error" in result or "not available" in result, f"[FAIL] Expected error for missing db, got: {result!r}"


def test_dream_list_memories_unknown_type():
    """dream_list_memories returns error for invalid memory_type."""
    db = _make_mock_db()
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_list_memories(memory_type="InvalidType"))
        assert "Error" in result or "unknown" in result, f"[FAIL] Expected error for unknown type, got: {result!r}"


def test_dream_list_memories_returns_formatted_list():
    """dream_list_memories returns JSON list of memory dicts."""
    mem = _make_mem_obj("EpisodicMemory", "mem-1", "Test memory")
    record = MagicMock()
    record.__getitem__.side_effect = lambda key, m=mem, label="EpisodicMemory": (
        m if key == "m" else label
    )
    rec_iter = MagicMock()
    rec_iter.__aiter__.return_value = [record]

    session = _make_mock_session(rec_iter)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver
    db._node_to_memory_object.return_value = mem

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_list_memories(memory_type="EpisodicMemory", limit=5))
        parsed = json.loads(result)
        assert isinstance(parsed, list), f"[FAIL] Expected list, got {type(parsed)}"
        assert len(parsed) == 1, f"[FAIL] Expected 1 result, got {len(parsed)}"
        assert parsed[0].get("id") == "mem-1", f"[FAIL] Expected id='mem-1', got {parsed[0]}"
        assert parsed[0].get("memory_type") == "EpisodicMemory", f"[FAIL] Expected memory_type='EpisodicMemory', got {parsed[0]}"
        assert "summaryEmbeddingVector" not in parsed[0], f"[FAIL] summaryEmbeddingVector should be stripped from output"


def test_dream_list_memories_all_types():
    """dream_list_memories memory_type='all' iterates over all labels."""
    mem = _make_mem_obj("EpisodicMemory", "mem-1", "Test memory")
    record = MagicMock()
    record.__getitem__.side_effect = lambda key, m=mem, label="EpisodicMemory": (
        m if key == "m" else label
    )
    rec_iter = MagicMock()
    rec_iter.__aiter__.return_value = [record]

    session = _make_mock_session(rec_iter)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver
    db._node_to_memory_object.return_value = mem

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_list_memories(memory_type="all", limit=3))
        parsed = json.loads(result)
        # 3 types each return 1 record
        assert len(parsed) == 3, f"[FAIL] Expected 3 results for 'all', got {len(parsed)}"


def test_dream_list_memories_handles_exception():
    """dream_list_memories catches exceptions and returns error string."""
    db = _make_mock_db()
    db.get_driver.side_effect = RuntimeError("Neo4j connection refused")

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_list_memories(memory_type="EpisodicMemory"))
        assert "Error" in result, f"[FAIL] Expected error string, got: {result!r}"


# ── dream_search_memories tests ──────────────────────────────────────

def test_dream_search_memories_db_not_available():
    """dream_search_memories returns error when memory_db is None."""
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = None
        result = asyncio.run(dream_search_memories(query="test"))
        assert "Error" in result or "not available" in result, f"[FAIL] Expected error for missing db, got: {result!r}"


def test_dream_search_memories_with_query():
    """dream_search_memories returns scored results."""
    mem = _make_mem_obj("EpisodicMemory", "mem-search", "searchable text")
    db = _make_mock_db()
    db.search = AsyncMock(return_value=[(mem, 0.95)])

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_search_memories(query="searchable", limit=5))
        parsed = json.loads(result)
        assert len(parsed) == 1, f"[FAIL] Expected 1 result, got {len(parsed)}"
        assert parsed[0]["score"] == 0.95, f"[FAIL] Expected score 0.95, got {parsed[0]['score']}"
        assert parsed[0]["memory_type"] == "EpisodicMemory", f"[FAIL] Expected EpisodicMemory type, got {parsed[0]['memory_type']}"
        assert "summaryEmbeddingVector" not in parsed[0]["memory"], f"[FAIL] summaryEmbeddingVector should be stripped"
        db.search.assert_called_once_with(query="searchable", top_k=5)


def test_dream_search_memories_handles_exception():
    """dream_search_memories catches search exceptions."""
    db = _make_mock_db()
    db.search = AsyncMock(side_effect=RuntimeError("Search index failure"))

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_search_memories(query="test"))
        assert "Error" in result, f"[FAIL] Expected error string, got: {result!r}"


# ── dream_get_memory tests ───────────────────────────────────────────

def test_dream_get_memory_db_not_available():
    """dream_get_memory returns error when memory_db is None."""
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = None
        result = asyncio.run(dream_get_memory(memory_id="abc", memory_type="EpisodicMemory"))
        assert "Error" in result or "not available" in result, f"[FAIL] Expected error for missing db, got: {result!r}"


def test_dream_get_memory_invalid_type():
    """dream_get_memory returns error for invalid memory_type."""
    db = _make_mock_db()
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_get_memory(memory_id="abc", memory_type="BadType"))
        assert "Error" in result or "invalid" in result, f"[FAIL] Expected error for invalid type, got: {result!r}"


def test_dream_get_memory_by_id():
    """dream_get_memory returns full JSON for a memory by ID."""
    mem = _make_mem_obj("EpisodicMemory", "target-id", "Target memory text")
    record = MagicMock()
    record.__getitem__.side_effect = lambda key: {"m": mem, "type": "EpisodicMemory"}[key]
    res = MagicMock()
    res.single = AsyncMock(return_value=record)

    session = _make_mock_session(res)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver
    db._node_to_memory_object.return_value = mem

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_get_memory(memory_id="target-id", memory_type="EpisodicMemory"))
        parsed = json.loads(result)
        assert parsed.get("id") == "target-id", f"[FAIL] Expected id='target-id', got {parsed}"
        assert parsed.get("memory_type") == "EpisodicMemory", f"[FAIL] Expected memory_type='EpisodicMemory', got {parsed}"
        assert "summaryEmbeddingVector" not in parsed, f"[FAIL] summaryEmbeddingVector should be stripped"


def test_dream_get_memory_not_found():
    """dream_get_memory returns 'No ... found' when record is None."""
    res = MagicMock()
    res.single = AsyncMock(return_value=None)

    session = _make_mock_session(res)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_get_memory(memory_id="nonexistent", memory_type="EpisodicMemory"))
        assert "No EpisodicMemory found" in result, f"[FAIL] Expected 'No EpisodicMemory found', got: {result!r}"


# ── dream_update_memory tests ────────────────────────────────────────

def test_dream_update_memory_db_not_available():
    """dream_update_memory returns error when memory_db is None."""
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = None
        result = asyncio.run(
            dream_update_memory(memory_id="abc", memory_type="EpisodicMemory", new_content="new")
        )
        assert "Error" in result or "not available" in result, f"[FAIL] Expected error for missing db, got: {result!r}"


def test_dream_update_memory_invalid_type():
    """dream_update_memory returns error for invalid memory_type."""
    db = _make_mock_db()
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(
            dream_update_memory(memory_id="abc", memory_type="BadType", new_content="new")
        )
        assert "Error" in result or "invalid" in result, f"[FAIL] Expected error for invalid type, got: {result!r}"


def test_dream_update_memory_changes_fields():
    """dream_update_memory updates the correct text field per type."""
    record = MagicMock()
    record.__getitem__.side_effect = lambda key, mid="abc": mid if key == "updated_id" else None
    res = MagicMock()
    res.single = AsyncMock(return_value=record)

    session = _make_mock_session(res)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(
            dream_update_memory(
                memory_id="abc", memory_type="EpisodicMemory", new_content="Updated summary text"
            )
        )
        assert "Updated EpisodicMemory abc" in result, f"[FAIL] Expected confirmation, got: {result!r}"
        assert "summary" in result, f"[FAIL] Expected 'summary' field mention, got: {result!r}"
        db._encode.assert_called_once_with("Updated summary text")


def test_dream_update_memory_knowledge_unit():
    """dream_update_memory uses 'statement' field for KnowledgeUnit."""
    record = MagicMock()
    record.__getitem__.return_value = "k-123"
    res = MagicMock()
    res.single = AsyncMock(return_value=record)

    session = _make_mock_session(res)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(
            dream_update_memory(
                memory_id="k-123", memory_type="KnowledgeUnit", new_content="Updated statement"
            )
        )
        assert "statement" in result, f"[FAIL] Expected 'statement' field mention, got: {result!r}"


def test_dream_update_memory_not_found():
    """dream_update_memory returns not-found when no record matches."""
    res = MagicMock()
    res.single = AsyncMock(return_value=None)

    session = _make_mock_session(res)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(
            dream_update_memory(
                memory_id="nonexistent", memory_type="EpisodicMemory", new_content="blah"
            )
        )
        assert "No EpisodicMemory found" in result, f"[FAIL] Expected 'No EpisodicMemory found', got: {result!r}"


def test_dream_update_memory_handles_exception():
    """dream_update_memory catches encoding/DB exceptions."""
    db = _make_mock_db()
    db._encode.side_effect = RuntimeError("Model not loaded")

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(
            dream_update_memory(
                memory_id="abc", memory_type="EpisodicMemory", new_content="blah"
            )
        )
        assert "Error" in result, f"[FAIL] Expected error string, got: {result!r}"


# ── dream_delete_memory tests ────────────────────────────────────────

def test_dream_delete_memory_db_not_available():
    """dream_delete_memory returns error when memory_db is None."""
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = None
        result = asyncio.run(
            dream_delete_memory(memory_id="abc", memory_type="EpisodicMemory")
        )
        assert "Error" in result or "not available" in result, f"[FAIL] Expected error for missing db, got: {result!r}"


def test_dream_delete_memory_invalid_type():
    """dream_delete_memory returns error for invalid memory_type."""
    db = _make_mock_db()
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(
            dream_delete_memory(memory_id="abc", memory_type="BadType")
        )
        assert "Error" in result or "invalid" in result, f"[FAIL] Expected error for invalid type, got: {result!r}"


def test_dream_delete_memory_by_id():
    """dream_delete_memory deletes and returns confirmation."""
    record = MagicMock()
    record.__getitem__.side_effect = lambda key: 1 if key == "deleted" else None
    res = MagicMock()
    res.single = AsyncMock(return_value=record)

    session = _make_mock_session(res)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(
            dream_delete_memory(
                memory_id="del-me", memory_type="EpisodicMemory", reason="stale duplicate"
            )
        )
        assert "Deleted EpisodicMemory del-me" in result, f"[FAIL] Expected deletion confirmation, got: {result!r}"


def test_dream_delete_memory_not_found():
    """dream_delete_memory returns empty string when nothing was deleted."""
    record = MagicMock()
    record.__getitem__.return_value = 0
    res = MagicMock()
    res.single = AsyncMock(return_value=record)

    session = _make_mock_session(res)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(
            dream_delete_memory(memory_id="nonexistent", memory_type="EpisodicMemory")
        )
        # When count is 0, the conditional skips; result should not contain "Deleted"
        assert "Deleted" not in result, f"[FAIL] Should not confirm deletion when count=0, got: {result!r}"


# ── dream_merge_memories tests ───────────────────────────────────────

def test_dream_merge_memories_success():
    """dream_merge_memories updates keep + deletes delete and returns combined result."""
    # Mock for dream_update_memory (keep)
    update_record = MagicMock()
    update_record.__getitem__.return_value = "keep-1"
    update_res = MagicMock()
    update_res.single = AsyncMock(return_value=update_record)
    update_session = _make_mock_session(update_res)

    # Mock for dream_delete_memory (delete)
    delete_record = MagicMock()
    delete_record.__getitem__.return_value = 1
    delete_res = MagicMock()
    delete_res.single = AsyncMock(return_value=delete_record)
    delete_session = _make_mock_session(delete_res)

    db = _make_mock_db()
    driver = MagicMock()
    driver.session.side_effect = [update_session, delete_session]
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(
            dream_merge_memories(
                keep_id="keep-1",
                keep_type="EpisodicMemory",
                delete_id="del-2",
                delete_type="KnowledgeUnit",
                merged_content="Merged content text",
            )
        )
        assert "Merged:" in result, f"[FAIL] Expected 'Merged:' prefix, got: {result!r}"
        assert "Updated EpisodicMemory keep-1" in result, f"[FAIL] Expected update confirmation, got: {result!r}"
        assert "Deleted KnowledgeUnit del-2" in result, f"[FAIL] Expected delete confirmation, got: {result!r}"


def test_dream_merge_memories_update_fails():
    """dream_merge_memories returns error if update step fails."""
    db = _make_mock_db()
    db.get_driver.return_value = None
    # g_data.get returns db but get_driver() returns None, causing update to fail

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(
            dream_merge_memories(
                keep_id="keep-1",
                keep_type="EpisodicMemory",
                delete_id="del-2",
                delete_type="KnowledgeUnit",
                merged_content="Merged content",
            )
        )
        assert result.startswith("Error"), f"[FAIL] Expected error from failed update, got: {result!r}"


# ── dream_get_stats tests ────────────────────────────────────────────

def test_dream_get_stats_db_not_available():
    """dream_get_stats returns error when memory_db is None."""
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = None
        result = asyncio.run(dream_get_stats())
        assert "Error" in result or "not available" in result, f"[FAIL] Expected error for missing db, got: {result!r}"


def test_dream_get_stats_returns_counts():
    """dream_get_stats returns JSON with counts per type and total."""
    def _make_count_record(n):
        r = MagicMock()
        r.__getitem__.return_value = n
        res = MagicMock()
        res.single = AsyncMock(return_value=r)
        return res

    # Each MEMORY_TYPES key gets a different count
    counts = {"EpisodicMemory": 5, "KnowledgeUnit": 3, "ProceduralUnit": 2}
    run_results = [_make_count_record(counts[t]) for t in db_keys] if False else None

    # Build fresh: session.run called 3 times
    records = [_make_count_record(counts[t]) for t in ["EpisodicMemory", "KnowledgeUnit", "ProceduralUnit"]]
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.run = AsyncMock(side_effect=records)

    driver = _make_mock_driver(session)
    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_get_stats())
        parsed = json.loads(result)
        assert parsed.get("EpisodicMemory") == 5, f"[FAIL] Expected EpisodicMemory=5, got {parsed}"
        assert parsed.get("KnowledgeUnit") == 3, f"[FAIL] Expected KnowledgeUnit=3, got {parsed}"
        assert parsed.get("ProceduralUnit") == 2, f"[FAIL] Expected ProceduralUnit=2, got {parsed}"
        assert parsed.get("total") == 10, f"[FAIL] Expected total=10, got {parsed}"


def test_dream_get_stats_handles_exception():
    """dream_get_stats catches exceptions gracefully."""
    db = _make_mock_db()
    db.get_driver.side_effect = RuntimeError("DB down")

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_get_stats())
        assert "Error" in result, f"[FAIL] Expected error string, got: {result!r}"


# ── dream_find_important_memories tests ───────────────────────────────

def test_dream_find_important_memories_db_not_available():
    """dream_find_important_memories returns error when memory_db is None."""
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = None
        result = asyncio.run(dream_find_important_memories())
        assert "Error" in result or "not available" in result, f"[FAIL] Expected error for missing db, got: {result!r}"


def test_dream_find_important_memories_returns_scored_list():
    """dream_find_important_memories returns JSON list sorted by cross_ref_score desc."""
    def _make_record(mem_id, content, cross_ref_score, importance, concept_count):
        record = MagicMock()
        record.__getitem__.side_effect = lambda key, _vals={
            "memory_id": mem_id, "memory_type": "EpisodicMemory",
            "content": content, "cross_ref_score": cross_ref_score,
            "importance": importance, "concept_count": concept_count,
        }: _vals[key]
        return record

    records = [
        _make_record("mem-aaa", "Important fact A", 3.5, 0.9, 3),
        _make_record("mem-bbb", "Important fact B", 2.0, 0.8, 2),
        _make_record("mem-ccc", "Low score C", 0.5, 0.5, 0),
    ]
    rec_iter = MagicMock()
    rec_iter.__aiter__.return_value = records

    session = _make_mock_session(rec_iter)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.MEMORY_TYPES = {"EpisodicMemory": (MagicMock(), "summary")}
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_find_important_memories(limit=5))
        parsed = json.loads(result)
        assert len(parsed) == 3, f"[FAIL] Expected 3 results, got {len(parsed)}"
        assert parsed[0]["memory_id"] == "mem-aaa"
        assert parsed[0]["cross_ref_score"] == 3.5
        assert parsed[2]["memory_id"] == "mem-ccc"
        assert parsed[2]["cross_ref_score"] == 0.5


def test_dream_find_important_memories_handles_exception():
    """dream_find_important_memories catches exceptions gracefully."""
    db = _make_mock_db()
    db.get_driver.side_effect = RuntimeError("DB down")

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_find_important_memories())
        assert "Error" in result, f"[FAIL] Expected error string, got: {result!r}"


# ── dream_replay_memory tests ─────────────────────────────────────────

def test_dream_replay_memory_db_not_available():
    """dream_replay_memory returns error when memory_db is None."""
    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = None
        result = asyncio.run(dream_replay_memory("mem-1", "EpisodicMemory"))
        assert "Error" in result or "not available" in result, f"[FAIL] Expected error for missing db, got: {result!r}"


def test_dream_replay_memory_invalid_type():
    """dream_replay_memory returns error for invalid memory_type."""
    db = _make_mock_db()

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_replay_memory("mem-1", "InvalidType"))
        assert "Error" in result, f"[FAIL] Expected error for invalid type, got: {result!r}"


def test_dream_replay_memory_not_found():
    """dream_replay_memory returns not-found message for missing ID."""
    fetch_res = MagicMock()
    fetch_res.single = AsyncMock(return_value=None)

    session = _make_mock_session(fetch_res)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_replay_memory("missing-id", "EpisodicMemory"))
        assert "No " in result or "not found" in result.lower(), f"[FAIL] Expected not-found message, got: {result!r}"


def test_dream_replay_memory_replays_and_boosts():
    """dream_replay_memory re-encodes embedding and boosts importance."""
    fetch_res = MagicMock()
    fetch_res.single = AsyncMock(return_value={
        "content": "Core knowledge about AI",
        "importance": 0.7,
    })

    update_res = MagicMock()
    update_res.single = AsyncMock(return_value={"updated_id": "mem-1"})

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.run = AsyncMock(side_effect=[fetch_res, update_res])

    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_replay_memory("mem-1", "EpisodicMemory"))

    assert "Replayed" in result, f"[FAIL] Expected 'Replayed' in result, got: {result!r}"
    assert "0.700" in result, f"[FAIL] Expected old importance 0.700, got: {result!r}"
    assert "0.805" in result, f"[FAIL] Expected new importance ~0.805 (0.7×1.15), got: {result!r}"
    db._encode.assert_called_once_with("Core knowledge about AI")


def test_dream_replay_memory_no_content():
    """dream_replay_memory returns error when content is empty/None."""
    fetch_res = MagicMock()
    fetch_res.single = AsyncMock(return_value={
        "content": None, "importance": 0.5,
    })

    session = _make_mock_session(fetch_res)
    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_replay_memory("mem-1", "EpisodicMemory"))
        assert "no content" in result.lower(), f"[FAIL] Expected no-content message, got: {result!r}"


def test_dream_replay_memory_importance_capped_at_one():
    """dream_replay_memory caps importance at 1.0."""
    fetch_res = MagicMock()
    fetch_res.single = AsyncMock(return_value={
        "content": "Very important memory",
        "importance": 0.95,
    })

    update_res = MagicMock()
    update_res.single = AsyncMock(return_value={"updated_id": "mem-1"})

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.run = AsyncMock(side_effect=[fetch_res, update_res])

    driver = _make_mock_driver(session)

    db = _make_mock_db()
    db.get_driver.return_value = driver

    with patch("OllamaTools.dream_tools.g_data") as mock_g:
        mock_g.get.return_value = db
        result = asyncio.run(dream_replay_memory("mem-1", "EpisodicMemory"))

    assert "1.000" in result, f"[FAIL] Expected importance capped at 1.000, got: {result!r}"


# ── Runner ───────────────────────────────────────────────────────────
