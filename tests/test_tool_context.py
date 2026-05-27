"""
Tests for ToolContext — tool loading decoupled from chat request lifecycle.

Covers:
- for_chat() returns full tool set (backward compatible)
- for_heartbeat() filters by module-declared categories
- for_heartbeat() with empty categories returns no tools (fail-safe)
- schemas are stripped of func/safe/categories
- tool_map maps correct callables
- dream tools included in heartbeat but not in chat (as before)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.services.tool_context import ToolContext, _matches_categories, _strip_meta
from lib.configuration_file import ConfigurationFile
from lib.global_registry import g_data


@pytest.fixture(autouse=True)
def _setup_config():
    """Provide a minimal config with heartbeat module tool categories."""
    cfg = ConfigurationFile("test", data={
        "heartbeat": {
            "enabled": True,
            "tick_interval": 30,
            "modules": {
                "memory_grooming": {
                    "tools": ["memory_read", "memory_write"],
                },
                "dream": {
                    "tools": ["memory_read", "memory_write"],
                },
                "system_health": {
                    "tools": [],
                },
                "no_categories": {},
            },
        },
    })
    g_data._registry["cfg"] = cfg
    yield
    g_data.clear_key("cfg")


# ------------------------------------------------------------------
# _matches_categories unit tests
# ------------------------------------------------------------------

def test_matches_categories_intersection():
    tool = {"categories": ["memory_read"]}
    assert _matches_categories(tool, {"memory_read"})


def test_matches_categories_no_intersection():
    tool = {"categories": ["sandbox_dangerous"]}
    assert not _matches_categories(tool, {"memory_read", "memory_write"})


def test_matches_categories_empty_tool():
    tool = {"categories": []}
    assert not _matches_categories(tool, {"memory_read"})


def test_matches_categories_missing_key():
    tool = {"type": "function"}
    assert not _matches_categories(tool, {"memory_read"})


def test_matches_categories_multiple_allowed():
    tool = {"categories": ["memory_write"]}
    assert _matches_categories(tool, {"memory_read", "memory_write"})


# ------------------------------------------------------------------
# _strip_meta unit tests
# ------------------------------------------------------------------

def test_strip_meta_removes_internal_keys():
    tools = [
        {
            "type": "function",
            "safe": True,
            "categories": ["memory_read"],
            "func": lambda: "test",
            "function": {"name": "test_tool", "description": "desc", "parameters": {}},
        }
    ]
    stripped = _strip_meta(tools)
    assert len(stripped) == 1
    assert "func" not in stripped[0]
    assert "safe" not in stripped[0]
    assert "categories" not in stripped[0]
    assert "function" in stripped[0]


def test_strip_meta_preserves_type_and_function():
    tools = [
        {
            "type": "function",
            "function": {"name": "test_tool", "description": "desc", "parameters": {}},
        }
    ]
    stripped = _strip_meta(tools)
    assert stripped[0] == {
        "type": "function",
        "function": {"name": "test_tool", "description": "desc", "parameters": {}},
    }


# ------------------------------------------------------------------
# ToolContext integration tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_for_chat_loads_all_tools():
    """for_chat() returns tools including send_message, search_memory, schedule_task, etc."""
    ctx = await ToolContext.for_chat()
    assert len(ctx.tools) > 0
    assert len(ctx.schemas) == len(ctx.tools)

    tool_names = {t["function"]["name"] for t in ctx.tools}
    assert "send_message" in tool_names
    assert "search_memory" in tool_names
    assert "schedule_task" in tool_names


@pytest.mark.asyncio
async def test_for_chat_excludes_dream_tools():
    """Dream tools should NOT be in for_chat() — they're heartbeat-only."""
    ctx = await ToolContext.for_chat()
    tool_names = {t["function"]["name"] for t in ctx.tools}
    assert "dream_get_stats" not in tool_names
    assert "dream_list_memories" not in tool_names


@pytest.mark.asyncio
async def test_for_heartbeat_memory_grooming():
    """Memory grooming gets memory_read + memory_write tools (including dreams)."""
    ctx = await ToolContext.for_heartbeat("memory_grooming")
    assert len(ctx.tools) > 0

    tool_names = {t["function"]["name"] for t in ctx.tools}

    # Dream tools should be included
    assert "dream_get_stats" in tool_names
    assert "dream_list_memories" in tool_names
    assert "dream_search_memories" in tool_names
    assert "dream_get_memory" in tool_names
    assert "dream_update_memory" in tool_names
    assert "dream_delete_memory" in tool_names
    assert "dream_merge_memories" in tool_names

    # search_memory is memory_read so should be in
    assert "search_memory" in tool_names

    # Dangerous/irrelevant tools should NOT be in
    assert "run_bash" not in tool_names
    assert "reset_sandbox" not in tool_names
    assert "send_message" not in tool_names
    assert "schedule_task" not in tool_names


@pytest.mark.asyncio
async def test_for_heartbeat_empty_categories():
    """Modules with empty tools: [] get no tools at all."""
    ctx = await ToolContext.for_heartbeat("system_health")
    assert ctx.tools == []
    assert ctx.schemas == []
    assert ctx.tool_map == {}


@pytest.mark.asyncio
async def test_for_heartbeat_no_config():
    """Module with no 'tools' key in config gets empty ToolContext."""
    ctx = await ToolContext.for_heartbeat("no_categories")
    assert ctx.tools == []
    assert ctx.schemas == []
    assert ctx.tool_map == {}


@pytest.mark.asyncio
async def test_tool_map_has_all_callables():
    """Every tool in the context should have its callable in tool_map."""
    ctx = await ToolContext.for_chat()
    for tool in ctx.tools:
        name = tool["function"]["name"]
        assert name in ctx.tool_map
        assert callable(ctx.tool_map[name])


@pytest.mark.asyncio
async def test_schemas_are_stripped():
    """Provider schemas must never contain func, safe, or categories."""
    ctx = await ToolContext.for_chat()
    for schema in ctx.schemas:
        assert "func" not in schema
        assert "safe" not in schema
        assert "categories" not in schema


@pytest.mark.asyncio
async def test_for_heartbeat_schemas_are_stripped():
    """Heartbeat schemas must also be stripped of internal keys."""
    ctx = await ToolContext.for_heartbeat("dream")
    for schema in ctx.schemas:
        assert "func" not in schema
        assert "safe" not in schema
        assert "categories" not in schema


@pytest.mark.asyncio
async def test_for_chat_preserves_categories_in_tools():
    """Full tool list should preserve categories for filtering."""
    ctx = await ToolContext.for_chat()
    tools_with_cats = [t for t in ctx.tools if "categories" in t]
    assert len(tools_with_cats) > 0
    for tool in tools_with_cats:
        assert isinstance(tool["categories"], list)
