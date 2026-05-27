"""
Tests for lib/utils/dynamic_loader.py — DynamicLoader and ToolLoader

Covers:
- load_all() with directory not found
- load_all() with empty directory (no .py files)
- load_all() skips __init__.py and dream_*.py files
- load_all() applies filter_fn to exclude items
- load_all() handles corrupt modules / missing attributes gracefully
- _process_tool() strips extra keys, keeps required shape
- _process_tool() passthrough for non-dict / non-function-type inputs
- _process_tool() defaults safe=False, omits categories when absent
- load_tools() handles get_tool() returning list of dicts
- load_tools() skips non-callable items
- load_tools() convenience function at module level
"""

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.utils.dynamic_loader import DynamicLoader, ToolLoader, load_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_module(directory, name, content):
    """Write a .py file into a directory. Returns the Path."""
    p = Path(directory) / f"{name}.py"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# DynamicLoader.load_all() — directory not found / empty
# ---------------------------------------------------------------------------

def test_load_all_directory_not_found():
    """load_all() returns [] when directory does not exist."""

    loader = DynamicLoader("/nonexistent/path/12345", "get_tool")
    result = asyncio.run(loader.load_all())

    assert result == [], f"expected [], got {result}"


def test_load_all_empty_directory():
    """load_all() returns [] when directory has no .py files."""

    with tempfile.TemporaryDirectory() as tmpdir:
        loader = DynamicLoader(tmpdir, "get_tool")
        result = asyncio.run(loader.load_all())

        assert result == [], f"expected [], got {result}"


# ---------------------------------------------------------------------------
# DynamicLoader.load_all() — filtering / skipping
# ---------------------------------------------------------------------------

def test_load_all_skips_init_and_dream_prefix():
    """load_all() skips __init__.py and dream_*.py files."""

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_module(tmpdir, "__init__", "get_tool = lambda: {'name': 'init'}")
        _write_module(tmpdir, "dream_tools", "get_tool = lambda: {'name': 'dream'}")
        _write_module(tmpdir, "valid_tool", "get_tool = lambda: {'name': 'valid'}")

        loader = DynamicLoader(tmpdir, "get_tool")
        result = asyncio.run(loader.load_all())

        names = [r()['name'] for r in result if callable(r)]
        assert names == ['valid'], f"expected ['valid'], got {names}"


def test_load_all_with_filter():
    """load_all() applies filter_fn to exclude items."""

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_module(tmpdir, "tool_a", "get_tool = lambda: {'name': 'a', 'safe': True}")
        _write_module(tmpdir, "tool_b", "get_tool = lambda: {'name': 'b', 'safe': False}")

        loader = DynamicLoader(tmpdir, "get_tool")
        result = asyncio.run(loader.load_all(
            filter_fn=lambda fn: fn()['safe']
        ))

        names = [r()['name'] for r in result]
        assert names == ['a'], f"expected ['a'], got {names}"


# ---------------------------------------------------------------------------
# DynamicLoader.load_all() — error resilience
# ---------------------------------------------------------------------------

def test_load_all_with_corrupt_module():
    """load_all() catches exceptions from individual modules gracefully."""

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_module(tmpdir, "broken_tool", "raise RuntimeError('boom')\nget_tool = lambda: {}")
        _write_module(tmpdir, "good_tool", "get_tool = lambda: {'name': 'good'}")

        loader = DynamicLoader(tmpdir, "get_tool")
        result = asyncio.run(loader.load_all())

        if len(result) != 1:
            assert False, f"expected 1 item, got {len(result)}"
        assert result[0]()['name'] == 'good', f"wrong item loaded"


def test_load_all_with_missing_attribute():
    """load_all() returns nothing for modules without the target attribute."""

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_module(tmpdir, "no_attr", "x = 1")
        _write_module(tmpdir, "has_attr", "get_tool = lambda: {'name': 'found'}")

        loader = DynamicLoader(tmpdir, "get_tool")
        result = asyncio.run(loader.load_all())

        if len(result) != 1:
            assert False, f"expected 1 item, got {len(result)}"
        assert result[0]()['name'] == 'found', f"wrong item"


# ---------------------------------------------------------------------------
# ToolLoader._process_tool() — schema reshaping
# ---------------------------------------------------------------------------

def test_process_tool_strips_extra_keys():
    """_process_tool() keeps only type, safe, function, func, categories."""

    loader = ToolLoader()

    raw = {
        "type": "function",
        "safe": True,
        "function": {
            "name": "test_tool",
            "description": "does something",
            "parameters": {"type": "object", "properties": {}},
        },
        "func": lambda: None,
        "categories": ["test"],
        "extra_key": "should be removed",
        "another_extra": 42,
    }

    result = loader._process_tool(raw)

    if result["type"] != "function":
        assert False, f"type={result['type']!r}"
    if result["safe"] is not True:
        assert False, f"safe={result['safe']!r}"
    if result["function"]["name"] != "test_tool":
        assert False, f"function.name={result['function']['name']!r}"
    if result["categories"] != ["test"]:
        assert False, f"categories={result['categories']!r}"

    if "extra_key" in result:
        assert False, f"extra_key should be removed"
    if "another_extra" in result:
        assert False, f"another_extra should be removed"

    expected_fn_keys = {"name", "description", "parameters"}
    if set(result["function"].keys()) != expected_fn_keys:
        assert False, f"function keys: {set(result['function'].keys())}"



def test_process_tool_non_dict_passthrough():
    """_process_tool() returns non-dict input as-is."""

    loader = ToolLoader()
    if loader._process_tool("str") != "str":
        assert False, f"string"
    if loader._process_tool(42) != 42:
        assert False, f"int"
    if loader._process_tool(None) is not None:
        assert False, f"None"
    if loader._process_tool([]) != []:
        assert False, f"list"



def test_process_tool_non_function_type_passthrough():
    """_process_tool() returns non-'function' type dicts unchanged."""

    loader = ToolLoader()
    raw = {"type": "custom", "data": [1, 2, 3]}
    result = loader._process_tool(raw)

    assert result is raw


def test_process_tool_defaults_safe_to_false():
    """_process_tool() defaults safe to False when absent."""

    loader = ToolLoader()
    raw = {
        "type": "function",
        "function": {
            "name": "unsafe_tool",
            "description": "no safe key",
            "parameters": {"type": "object"},
        },
        "func": lambda: None,
    }

    result = loader._process_tool(raw)
    assert result.get("safe") is False, f"safe={result.get('safe')!r}"


def test_process_tool_without_categories():
    """_process_tool() omits categories when not in input."""

    loader = ToolLoader()
    raw = {
        "type": "function",
        "function": {
            "name": "no_cat",
            "description": "no categories",
            "parameters": {"type": "object"},
        },
        "func": lambda: None,
    }

    result = loader._process_tool(raw)
    assert not ("categories" in result)


def test_process_tool_preserves_func_none():
    """_process_tool() preserves func=None."""

    loader = ToolLoader()
    raw = {
        "type": "function",
        "function": {
            "name": "no_func",
            "description": "func is None",
            "parameters": {},
        },
        "func": None,
    }

    result = loader._process_tool(raw)
    assert result.get("func") is None, f"func={result.get('func')!r}"


# ---------------------------------------------------------------------------
# ToolLoader.load_tools() — list handling / non-callable filtering
# ---------------------------------------------------------------------------

def test_load_tools_handles_get_tool_returning_list():
    """load_tools() handles get_tool() returning a list of dicts."""

    with patch.object(ToolLoader, 'load_all', new_callable=AsyncMock) as mock_load:
        mock_load.return_value = [
            lambda: [
                {"type": "function", "function": {"name": "t1", "description": "a", "parameters": {}}, "func": None},
                {"type": "function", "function": {"name": "t2", "description": "b", "parameters": {}}, "func": None},
            ],
            lambda: {"type": "function", "function": {"name": "t3", "description": "c", "parameters": {}}, "func": None},
        ]
        loader = ToolLoader()
        result = asyncio.run(loader.load_tools())

        names = [t["function"]["name"] for t in result]
        assert names == ["t1", "t2", "t3"], f"expected ['t1','t2','t3'], got {names}"


def test_load_tools_skips_non_callable():
    """load_tools() skips non-callable items from load_all()."""

    with patch.object(ToolLoader, 'load_all', new_callable=AsyncMock) as mock_load:
        mock_load.return_value = [
            "not callable",
            lambda: {"type": "function", "function": {"name": "ok", "description": "x", "parameters": {}}, "func": None},
            None,
            42,
        ]
        loader = ToolLoader()
        result = asyncio.run(loader.load_tools())

        names = [t["function"]["name"] for t in result]
        assert names == ["ok"], f"expected ['ok'], got {names}"


# ---------------------------------------------------------------------------
# Module-level load_tools() convenience function
# ---------------------------------------------------------------------------

def test_load_tools_convenience():
    """load_tools() convenience function delegates to ToolLoader."""

    with patch.object(ToolLoader, 'load_tools', return_value=[
        {"type": "function", "function": {"name": "test"}, "safe": False, "func": None}
    ]):
        result = asyncio.run(load_tools())
        assert result == [{"type": "function", "function": {"name": "test"}, "safe": False, "func": None}], f"got {result}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))