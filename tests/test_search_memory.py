"""
Tests for OllamaTools/search_memory.py — memory search tool.

Covers:
- search_memory() with results, empty results, DB unavailable, exceptions
- get_tool() schema validation (type, safe, categories, function, func)
"""

import asyncio
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from OllamaTools.search_memory import search_memory, get_tool


def _parse_result(raw: str) -> dict:
    return json.loads(raw)


# ── search_memory() tests ──────────────────────────────────────────────


def test_search_memory_returns_formatted_results():
    """Mock memory_db.search() returning results → tool_success with structured data."""
    mock_db = MagicMock()
    mock_db.search = AsyncMock(return_value=[
        ("remembered something important", 0.95),
        ("another memory here", 0.82),
    ])

    with patch("OllamaTools.search_memory.g_data") as mock_g_data:
        mock_g_data.get.return_value = mock_db
        raw = asyncio.run(search_memory("what did I say about tests?"))

    result = _parse_result(raw)
    assert result.get("success"), f"[FAIL] Expected success=True, got {result}"
    assert result.get("query") == "what did I say about tests?", f"[FAIL] Expected query in response, got {result.get('query')!r}"
    data = result.get("data")
    assert isinstance(data, list) and len(data) == 2, f"[FAIL] Expected 2 results in data list, got {data!r}"
    assert data[0] == {"memory": "remembered something important", "similarity": "0.95"}, f"[FAIL] First result mismatch: {data[0]!r}"
    assert data[1] == {"memory": "another memory here", "similarity": "0.82"}, f"[FAIL] Second result mismatch: {data[1]!r}"
    mock_db.search.assert_called_once_with("what did I say about tests?", top_k=5, filter_user_id=None)


def test_search_memory_empty_results():
    """Mock memory_db.search() returns empty list → tool_success with empty data."""
    mock_db = MagicMock()
    mock_db.search = AsyncMock(return_value=[])

    with patch("OllamaTools.search_memory.g_data") as mock_g_data:
        mock_g_data.get.return_value = mock_db
        raw = asyncio.run(search_memory("nonexistent stuff"))

    result = _parse_result(raw)
    assert result.get("success"), f"[FAIL] Expected success=True for empty results, got {result}"
    assert result.get("data") == [], f"[FAIL] Expected empty data list, got {result.get('data')!r}"
    assert result.get("query") == "nonexistent stuff", f"[FAIL] Expected query preserved, got {result.get('query')!r}"


def test_search_memory_db_not_available():
    """g_data.get('memory_db') returns None → tool_error."""
    with patch("OllamaTools.search_memory.g_data") as mock_g_data:
        mock_g_data.get.return_value = None
        raw = asyncio.run(search_memory("anything"))

    result = _parse_result(raw)
    assert result.get("success") is False, f"[FAIL] Expected success=False, got {result}"
    assert "Memory database not available" in result.get("error", ""), f"[FAIL] Expected 'Memory database not available', got {result.get('error')!r}"
    assert result.get("query") == "anything", f"[FAIL] Expected query in error context, got {result.get('query')!r}"


def test_search_memory_db_is_falsy_but_not_none():
    """g_data.get('memory_db') returns falsy (e.g. empty string, 0) → tool_error."""
    for falsy_val in ("", 0, False):
        with patch("OllamaTools.search_memory.g_data") as mock_g_data:
            mock_g_data.get.return_value = falsy_val
            raw = asyncio.run(search_memory("something"))

        result = _parse_result(raw)
        assert result.get("success") is False, f"[FAIL] Expected success=False for falsy val {falsy_val!r}, got {result}"
        assert "Memory database not available" in result.get("error", ""), f"[FAIL] Expected error message for falsy val {falsy_val!r}"


def test_search_memory_exception_handled():
    """memory_db.search() raises exception → tool_error with exception message."""
    mock_db = MagicMock()
    mock_db.search = AsyncMock(side_effect=RuntimeError("Neo4j connection timeout"))

    with patch("OllamaTools.search_memory.g_data") as mock_g_data:
        mock_g_data.get.return_value = mock_db
        raw = asyncio.run(search_memory("crash test"))

    result = _parse_result(raw)
    assert result.get("success") is False, f"[FAIL] Expected success=False on exception, got {result}"
    assert "Neo4j connection timeout" in result.get("error", ""), f"[FAIL] Expected 'Neo4j connection timeout' in error, got {result.get('error')!r}"
    assert result.get("query") == "crash test", f"[FAIL] Expected query in error context"


def test_search_memory_non_exception_error_handled():
    """memory_db.search() raises BaseException (not Exception) propagates up."""
    mock_db = MagicMock()
    mock_db.search = AsyncMock(side_effect=KeyboardInterrupt())

    with patch("OllamaTools.search_memory.g_data") as mock_g_data:
        mock_g_data.get.return_value = mock_db

        with pytest.raises(KeyboardInterrupt):
            asyncio.run(search_memory("interrupt"))


def test_search_memory_preserves_similarity_type():
    """Similarity values are always stringified (str()) regardless of input type."""
    mock_db = MagicMock()
    mock_db.search = AsyncMock(return_value=[
        ("memory a", 0.9999),
        ("memory b", 42),  # int similarity
        ("memory c", None),  # None similarity
    ])

    with patch("OllamaTools.search_memory.g_data") as mock_g_data:
        mock_g_data.get.return_value = mock_db
        raw = asyncio.run(search_memory("test"))

    result = _parse_result(raw)
    data = result.get("data", [])
    assert data[0]["similarity"] == "0.9999", f"[FAIL] Float similarity not stringified: {data[0]['similarity']!r}"
    assert data[1]["similarity"] == "42", f"[FAIL] Int similarity not stringified: {data[1]['similarity']!r}"
    assert data[2]["similarity"] == "None", f"[FAIL] None similarity not stringified: {data[2]['similarity']!r}"


def test_search_memory_with_scoped_person_id():
    """person='discord:123' passes through directly as filter_user_id."""
    mock_db = MagicMock()
    mock_db.search = AsyncMock(return_value=[
        ("memory from discord user", 0.99),
    ])

    with patch("OllamaTools.search_memory.g_data") as mock_g_data:
        mock_g_data.get.return_value = mock_db
        raw = asyncio.run(search_memory("test query", person="discord:123"))

    result = _parse_result(raw)
    assert result.get("success"), f"[FAIL] Expected success=True, got {result}"
    data = result.get("data", [])
    assert len(data) == 1, f"[FAIL] Expected 1 result, got {len(data)}"
    mock_db.search.assert_called_once_with("test query", top_k=5, filter_user_id="discord:123")


def test_search_memory_with_empty_person():
    """person='' (default) does not apply any filter."""
    mock_db = MagicMock()
    mock_db.search = AsyncMock(return_value=[
        ("memory a", 0.9),
    ])

    with patch("OllamaTools.search_memory.g_data") as mock_g_data:
        mock_g_data.get.return_value = mock_db
        raw = asyncio.run(search_memory("test", person=""))

    result = _parse_result(raw)
    assert result.get("success"), f"[FAIL] Expected success=True, got {result}"
    # filter_user_id should be None (no filter) when person is empty
    mock_db.search.assert_called_once_with("test", top_k=5, filter_user_id=None)


def test_search_memory_default_no_person_arg():
    """Calling search_memory(query) without person arg uses default '' (no filter)."""
    mock_db = MagicMock()
    mock_db.search = AsyncMock(return_value=[])

    with patch("OllamaTools.search_memory.g_data") as mock_g_data:
        mock_g_data.get.return_value = mock_db
        raw = asyncio.run(search_memory("plain query"))

    result = _parse_result(raw)
    assert result.get("success"), f"[FAIL] Expected success=True, got {result}"
    mock_db.search.assert_called_once_with("plain query", top_k=5, filter_user_id=None)


def test_resolve_person_scoped_id():
    """_resolve_person with scoped ID returns it unchanged."""
    from OllamaTools.search_memory import _resolve_person

    mock_db = MagicMock()
    result = asyncio.run(_resolve_person(mock_db, "discord:123456"))
    assert result == "discord:123456", f"[FAIL] Expected 'discord:123456', got {result!r}"


class _AsyncRecordList(list):
    """Async-iterable list of records for mocking Neo4j result cursors."""
    async def __aiter__(self):
        for item in self:
            yield item


def _make_neo4j_record(user_id_value):
    """Create a mock Neo4j record that returns user_id_value for 'user_id' key."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: user_id_value if key == "user_id" else None
    return record


def test_resolve_person_name_match():
    """_resolve_person with name queries Neo4j and returns user_id."""
    from OllamaTools.search_memory import _resolve_person

    mock_record = _make_neo4j_record("discord:999")

    mock_session = MagicMock()
    mock_session.run = AsyncMock(return_value=_AsyncRecordList([mock_record]))

    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_driver.session.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_db = MagicMock()
    mock_db.get_driver.return_value = mock_driver

    result = asyncio.run(_resolve_person(mock_db, "testuser"))
    assert result == "discord:999", f"[FAIL] Expected 'discord:999', got {result!r}"


def test_resolve_person_name_not_found():
    """_resolve_person with unknown name returns None."""
    from OllamaTools.search_memory import _resolve_person

    mock_session = MagicMock()
    mock_session.run = AsyncMock(return_value=_AsyncRecordList([]))

    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_driver.session.return_value.__aexit__ = AsyncMock(return_value=None)

    mock_db = MagicMock()
    mock_db.get_driver.return_value = mock_driver

    result = asyncio.run(_resolve_person(mock_db, "nonexistent"))
    assert result is None, f"[FAIL] Expected None for unknown person, got {result!r}"


def test_resolve_person_exception_returns_none():
    """_resolve_person on Neo4j error returns None gracefully."""
    from OllamaTools.search_memory import _resolve_person

    mock_db = MagicMock()
    mock_db.get_driver.side_effect = RuntimeError("Neo4j down")

    result = asyncio.run(_resolve_person(mock_db, "testuser"))
    assert result is None, f"[FAIL] Expected None on exception, got {result!r}"


# ── get_tool() tests ───────────────────────────────────────────────────


def test_get_tool_has_correct_schema():
    """get_tool() returns properly structured tool definition."""
    tool = get_tool()[0]
    assert tool.get("type") == "function", f"[FAIL] Expected type='function', got {tool.get('type')!r}"
    assert tool.get("function"), f"[FAIL] Missing 'function' key"
    fn = tool["function"]
    assert fn.get("name") == "search_memory", f"[FAIL] Expected name='search_memory', got {fn.get('name')!r}"
    assert fn.get("description") == "Search the memory database for relevant past interactions.", f"[FAIL] Unexpected description: {fn.get('description')!r}"
    params = fn.get("parameters", {})
    assert params.get("type") == "object", f"[FAIL] Expected parameters.type='object'"
    assert "query" in params.get("properties", {}), f"[FAIL] Missing 'query' parameter"
    assert params.get("properties", {}).get("query", {}).get("type") == "string", f"[FAIL] 'query' parameter should be type='string'"
    assert "query" in params.get("required", []), f"[FAIL] 'query' should be required"
    assert "person" in params.get("properties", {}), f"[FAIL] Missing 'person' parameter in schema"
    assert params.get("properties", {}).get("person", {}).get("type") == "string", f"[FAIL] 'person' parameter should be type='string'"
    assert "person" not in params.get("required", []), f"[FAIL] 'person' should NOT be required"


def test_get_tool_safe_is_true():
    """get_tool() marks tool as safe=True (read-only memory operation)."""
    tool = get_tool()[0]
    assert tool.get("safe") is True, f"[FAIL] Expected safe=True, got {tool.get('safe')!r}"


def test_get_tool_has_memory_read_category():
    """get_tool() includes 'memory_read' in categories."""
    tool = get_tool()[0]
    cats = tool.get("categories", [])
    assert "memory_read" in cats, f"[FAIL] Expected 'memory_read' in categories, got {cats}"


def test_get_tool_func_is_callable():
    """get_tool() 'func' key points to callable search_memory."""
    tool = get_tool()[0]
    func = tool.get("func")
    assert callable(func), f"[FAIL] 'func' is not callable: {type(func)}"
