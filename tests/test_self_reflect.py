"""
Tests for OllamaTools/self_reflect.py — Self-model reflection tools.

Covers:
- get_tool() returns all 4 tool schemas correctly structured
- self_model_get — returns full self-model state (fields, skills, goals)
- self_model_get — handles missing node gracefully
- self_model_update — updates valid fields, creates node if needed
- self_model_update — rejects invalid fields
- self_skill_update — creates new skill with initial proficiency
- self_skill_update — updates existing skill with proficiency delta
- self_skill_update — clamps proficiency to [0.0, 1.0]
- self_goal_update — creates new goal, syncs active_goals
- self_goal_update — updates existing goal status
- self_goal_update — rejects invalid status
- Null memory_db graceful handling for all tools
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from OllamaTools.self_reflect import (
    self_model_get,
    self_model_update,
    self_skill_update,
    self_goal_update,
    get_tool,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_mock_db():
    """Create a mock MemoryDb with get_driver()."""
    db = MagicMock()
    return db


def _make_mock_session():
    """Create a mock neo4j session with AsyncMock run()."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.run = AsyncMock()
    return session


def _make_mock_driver(session):
    """Create a mock driver whose session() returns the given session."""
    driver = MagicMock()
    driver.session.return_value = session
    return driver


def _setup_db(db, session):
    """Wire up mock db with driver and session."""
    driver = _make_mock_driver(session)
    db.get_driver = MagicMock(return_value=driver)
    return db


def _self_model_node_dict():
    """Return a mock SelfModel node as a dict (what Neo4j returns)."""
    return {
        "name": "Nami",
        "current_mood": "neutral",
        "active_goals": [],
        "capability_assessment": "",
        "last_reflection_at": 0,
        "summaryEmbeddingVector": None,
    }


def _make_record(data: dict):
    """Create a MagicMock that behaves like a Neo4j record for given data."""
    rec = MagicMock()
    rec.__getitem__ = lambda s, k: data[k]
    rec.get = lambda k, d=None: data.get(k, d)

    # Support dict() conversion for _node_to_dict
    def mock_items():
        return iter(data.items())
    rec.items = mock_items
    return rec


def run(coro):
    """Synchronously run an async coroutine."""
    return asyncio.run(coro)


# ── get_tool() tests (sync) ────────────────────────────────────────────

def test_get_tool_returns_list():
    tools = get_tool()
    assert isinstance(tools, list)


def test_get_tool_has_four_schemas():
    tools = get_tool()
    assert len(tools) == 4


def test_get_tool_names():
    tools = get_tool()
    names = {t["function"]["name"] for t in tools}
    assert names == {"self_model_get", "self_model_update", "self_skill_update", "self_goal_update"}


def test_get_tool_each_has_func():
    for t in get_tool():
        assert callable(t["func"]), f"{t['function']['name']} missing callable func"


# ── self_model_get tests ─────────────────────────────────────────────

def test_self_model_get_returns_full_state():
    """self_model_get returns fields, skills, and goals."""
    db = _make_mock_db()
    session = _make_mock_session()
    node = _self_model_node_dict()

    async def fake_run(query, params=None):
        res = MagicMock()
        if "HAS_SKILL" in query:
            skill_rec = _make_record({"name": "Python", "proficiency": 0.8, "last_updated": 1000, "description": "Good at Python"})
            res.__aiter__ = lambda s: _async_iter([skill_rec])
        elif "HAS_GOAL" in query:
            goal_rec = _make_record({"description": "Learn WebRTC", "status": "active", "created_at": 2000, "updated_at": 2000})
            res.__aiter__ = lambda s: _async_iter([goal_rec])
        else:
            # _get_self_model_node does record["s"] — return record with key "s"
            record = _make_record({"s": node})
            res.single = AsyncMock(return_value=record)
        return res

    session.run = fake_run
    _setup_db(db, session)

    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = db
        result = run(self_model_get())

    data = json.loads(result)
    assert data["exists"] is True
    assert data["fields"]["name"] == "Nami"
    assert data["fields"]["current_mood"] == "neutral"
    assert len(data["skills"]) == 1
    assert data["skills"][0]["name"] == "Python"
    assert data["skills"][0]["proficiency"] == 0.8
    assert len(data["goals"]) == 1
    assert data["goals"][0]["description"] == "Learn WebRTC"


def test_self_model_get_no_node():
    """self_model_get returns exists=False when no node."""
    db = _make_mock_db()
    session = _make_mock_session()
    session.run = AsyncMock(return_value=_no_match_result())
    _setup_db(db, session)

    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = db
        result = run(self_model_get())

    data = json.loads(result)
    assert data["exists"] is False
    assert "No SelfModel node found" in data["message"]


def test_self_model_get_no_memory_db():
    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = None
        result = run(self_model_get())
    assert result.startswith("Error: memory_db not available")


# ── self_model_update tests ──────────────────────────────────────────

def test_self_model_update_valid_field():
    db = _make_mock_db()
    session = _make_mock_session()
    _setup_db(db, session)

    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = db
        result = run(self_model_update("current_mood", "content"))

    assert "Updated self-model field 'current_mood'" in result


def test_self_model_update_invalid_field():
    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = _make_mock_db()
        result = run(self_model_update("nonexistent", "value"))

    assert "Error: invalid field" in result


def test_self_model_update_no_memory_db():
    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = None
        result = run(self_model_update("current_mood", "happy"))
    assert result.startswith("Error: memory_db not available")


# ── self_skill_update tests ──────────────────────────────────────────

def test_self_skill_update_creates_new_skill():
    """Creating a new skill starts at 0.1 + delta."""
    db = _make_mock_db()
    session = _make_mock_session()

    async def fake_run(query, params=None):
        res = MagicMock()
        rec = _make_record({"old_prof": 0.1, "new_prof": 0.25})
        res.single = AsyncMock(return_value=rec)
        return res

    session.run = fake_run
    _setup_db(db, session)

    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = db
        result = run(self_skill_update("WebRTC", 0.15, "Researched successfully"))

    assert "Skill 'WebRTC'" in result
    assert "0.100" in result
    assert "0.250" in result


def test_self_skill_update_improves_existing():
    """Existing skill proficiency increases by delta."""
    db = _make_mock_db()
    session = _make_mock_session()

    async def fake_run(query, params=None):
        res = MagicMock()
        rec = _make_record({"old_prof": 0.5, "new_prof": 0.6})
        res.single = AsyncMock(return_value=rec)
        return res

    session.run = fake_run
    _setup_db(db, session)

    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = db
        result = run(self_skill_update("Python", 0.1, "Improved async patterns"))

    assert "0.500" in result
    assert "0.600" in result


def test_self_skill_update_clamps_at_max():
    """Proficiency cannot exceed 1.0."""
    db = _make_mock_db()
    session = _make_mock_session()

    async def fake_run(query, params=None):
        res = MagicMock()
        rec = _make_record({"old_prof": 0.95, "new_prof": 1.0})
        res.single = AsyncMock(return_value=rec)
        return res

    session.run = fake_run
    _setup_db(db, session)

    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = db
        result = run(self_skill_update("Python", 0.5))

    assert "1.000" in result


def test_self_skill_update_clamps_at_min():
    """Proficiency cannot go below 0.0."""
    db = _make_mock_db()
    session = _make_mock_session()

    async def fake_run(query, params=None):
        res = MagicMock()
        rec = _make_record({"old_prof": 0.05, "new_prof": 0.0})
        res.single = AsyncMock(return_value=rec)
        return res

    session.run = fake_run
    _setup_db(db, session)

    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = db
        result = run(self_skill_update("OldSkill", -1.0))

    assert "0.000" in result


def test_self_skill_update_no_memory_db():
    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = None
        result = run(self_skill_update("Test", 0.1))
    assert result.startswith("Error: memory_db not available")


# ── self_goal_update tests ───────────────────────────────────────────

def test_self_goal_update_creates_new_goal():
    db = _make_mock_db()
    session = _make_mock_session()

    async def fake_run(query, params=None):
        res = MagicMock()
        if "collect(g.description)" in query:
            rec = _make_record({"goals": ["Learn Rust"]})
            res.single = AsyncMock(return_value=rec)
        else:
            rec = _make_record({"status": "active", "created_at": 5000})
            res.single = AsyncMock(return_value=rec)
        return res

    session.run = fake_run
    _setup_db(db, session)

    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = db
        result = run(self_goal_update("Learn Rust", "active"))

    assert "Goal 'Learn Rust'" in result
    assert "active" in result


def test_self_goal_update_changes_status():
    db = _make_mock_db()
    session = _make_mock_session()

    async def fake_run(query, params=None):
        res = MagicMock()
        if "collect(g.description)" in query:
            rec = _make_record({"goals": []})
            res.single = AsyncMock(return_value=rec)
        else:
            rec = _make_record({"status": "achieved", "created_at": 5000})
            res.single = AsyncMock(return_value=rec)
        return res

    session.run = fake_run
    _setup_db(db, session)

    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = db
        result = run(self_goal_update("Learn Rust", "achieved"))

    assert "achieved" in result


def test_self_goal_update_rejects_invalid_status():
    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = _make_mock_db()
        result = run(self_goal_update("Learn Rust", "invalid_status"))

    assert "Error: invalid status" in result


def test_self_goal_update_no_memory_db():
    with patch("OllamaTools.self_reflect.g_data") as mock_g:
        mock_g.get.return_value = None
        result = run(self_goal_update("Learn Rust"))
    assert result.startswith("Error: memory_db not available")


# ── Helpers for async iteration ──────────────────────────────────────

async def _async_iter(items):
    for item in items:
        yield item


def _no_match_result():
    """Return a mock result with single() returning None."""
    res = MagicMock()
    res.single = AsyncMock(return_value=None)
    return res


# ── Run ──────────────────────────────────────────────────────────────
