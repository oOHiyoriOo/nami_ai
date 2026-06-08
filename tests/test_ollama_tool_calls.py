"""
Tests for OllamaProvider._validate_tool_calls and _normalize_tool_call.

Covers _normalize_tool_call (direct unit tests):
- Dict passthrough: dict input returned as-is
- Pydantic model_dump(): objects with model_dump() converted
- Fallback from attributes: reconstruct from function.name/function.arguments
- Last-resort fallback: dict(call) where possible, raw call otherwise

Covers _validate_tool_calls:
- Valid list of dict tool calls → returned as-is
- Pydantic ToolCall objects with model_dump() → converted to plain dicts
- Fallback: objects without model_dump() but with function attribute → reconstructed
- Non-list input → raises ValueError
- Non-dict entry (string, int) → raises ValueError
- Tool call missing function key → raises ValueError
- function value is not a dict → raises ValueError
- Function missing name key → raises ValueError
- Function name is not a string → raises ValueError
- Function missing arguments key → raises ValueError
- Function arguments is not a dict → raises ValueError
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_provider():
    """Create an OllamaProvider with a mocked OllamaClient."""
    with patch("lib.ai_providers.ollama_provider.OllamaClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client.show = MagicMock(return_value={"capabilities": ["completion"]})
        mock_client_class.return_value = mock_client
        from lib.ai_providers.ollama_provider import OllamaProvider
        return OllamaProvider({"url": "http://localhost:11434", "model": "llama3.2"})


# ── valid input — plain dicts ───────────────────────────────────────────────

def test_valid_dict_tool_calls():
    """Valid list of dict tool calls returned as-is."""
    p = _make_provider()
    tool_calls = [{"function": {"name": "my_tool", "arguments": {"x": 1}}}]
    result = p._validate_tool_calls(tool_calls)
    assert result == tool_calls, f"got {result}"


def test_multiple_valid_tool_calls():
    """Multiple valid tool calls all returned."""
    p = _make_provider()
    tool_calls = [
        {"function": {"name": "tool_a", "arguments": {}}},
        {"function": {"name": "tool_b", "arguments": {"key": [1, 2]}}},
    ]
    result = p._validate_tool_calls(tool_calls)
    assert result == tool_calls, f"got {result}"


# ── Pydantic ToolCall with model_dump() ────────────────────────────────────

def test_pydantic_toolcall_model_dump():
    """Ollama Pydantic ToolCall objects with model_dump() → converted to plain dicts."""
    p = _make_provider()
    expected = {"function": {"name": "search", "arguments": {"query": "test"}}}

    mock_call = MagicMock()
    mock_call.model_dump.return_value = expected

    result = p._validate_tool_calls([mock_call])
    assert result == [expected], f"got {result}"


# ── Fallback objects with function attribute ────────────────────────────────

def test_fallback_function_attribute():
    """Objects without model_dump() but with function attribute → reconstructed."""
    p = _make_provider()

    mock_fn = MagicMock()
    mock_fn.name = "my_func"
    mock_fn.arguments = {"p1": "v1"}

    mock_call = MagicMock(spec=[])  # No model_dump
    mock_call.function = mock_fn

    result = p._validate_tool_calls([mock_call])
    assert result == [{"function": {"name": "my_func", "arguments": {"p1": "v1"}}}], f"got {result}"


def test_fallback_function_attr_with_get():
    """Fallback: function attribute uses .get() for non-attribute access."""
    p = _make_provider()

    mock_fn = MagicMock()
    mock_fn.name = "dynamic_tool"
    # arguments available via attributes but no .get
    mock_fn.arguments = {"flag": True}

    mock_call = MagicMock(spec=[])  # No model_dump
    mock_call.function = mock_fn

    result = p._validate_tool_calls([mock_call])
    assert result == [{"function": {"name": "dynamic_tool", "arguments": {"flag": True}}}], f"got {result}"


# ── non-list input ──────────────────────────────────────────────────────────

def test_non_list_input_raises():
    """Non-list input → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls({"not": "a list"})
        assert False, f"no exception raised"
    except ValueError as e:
        assert "list" in str(e).lower(), f"wrong message: {e}"


def test_none_input_raises():
    """None input → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls(None)
        assert False, f"no exception raised"
    except ValueError:
        pass


# ── non-dict entry ───────────────────────────────────────────────────────────

def test_non_dict_entry_string():
    """String entry in tool_calls list → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls(["not_a_dict"])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "dict" in str(e) or "str" in str(e), f"wrong message: {e}"


def test_non_dict_entry_int():
    """Int entry in tool_calls list → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls([42])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "dict" in str(e) or "int" in str(e), f"wrong message: {e}"


# ── missing function key ────────────────────────────────────────────────────

def test_missing_function_key():
    """Tool call missing 'function' key → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls([{"other": "data"}])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "function" in str(e).lower(), f"wrong message: {e}"


# ── function not a dict ─────────────────────────────────────────────────────

def test_function_not_dict():
    """function value is not a dict → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls([{"function": "not_a_dict"}])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "function" in str(e).lower(), f"wrong message: {e}"


def test_function_is_list():
    """function value is a list → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls([{"function": [1, 2, 3]}])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "dict" in str(e), f"wrong message: {e}"


# ── function missing name ───────────────────────────────────────────────────

def test_function_missing_name():
    """Function missing 'name' key → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls([{"function": {"arguments": {}}}])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "name" in str(e).lower(), f"wrong message: {e}"


# ── function name not a string ──────────────────────────────────────────────

def test_function_name_not_string():
    """Function name is not a string → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls([{"function": {"name": 42, "arguments": {}}}])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "string" in str(e), f"wrong message: {e}"


def test_function_name_is_none():
    """Function name is None → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls([{"function": {"name": None, "arguments": {}}}])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "string" in str(e), f"wrong message: {e}"


# ── function missing arguments ──────────────────────────────────────────────

def test_function_missing_arguments():
    """Function missing 'arguments' key → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls([{"function": {"name": "tool_x"}}])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "arguments" in str(e).lower(), f"wrong message: {e}"


# ── function arguments not a dict ───────────────────────────────────────────

def test_function_arguments_not_dict():
    """Function arguments is not a dict → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls([{"function": {"name": "tool_y", "arguments": [1,2,3]}}])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "dict" in str(e), f"wrong message: {e}"


def test_function_arguments_is_none():
    """Function arguments is None → raises ValueError."""
    p = _make_provider()
    try:
        p._validate_tool_calls([{"function": {"name": "tool_z", "arguments": None}}])
        assert False, f"no exception raised"
    except ValueError as e:
        assert "dict" in str(e), f"wrong message: {e}"


# ── _normalize_tool_call direct unit tests ───────────────────────────────────

def test_normalize_tool_call_passthrough_dict():
    """Dict input passes through unchanged."""
    p = _make_provider()
    call = {"function": {"name": "t", "arguments": {"x": 1}}}
    result = p._normalize_tool_call(call)
    if result is not call:
        assert False, f"got {result}, expected same object"
    assert result == call, f"mutated: {result}"


def test_normalize_tool_call_pydantic_model_dump():
    """Object with model_dump() → converted to dict."""
    p = _make_provider()
    expected = {"function": {"name": "search", "arguments": {"q": "test"}}}
    mock_call = MagicMock()
    mock_call.model_dump.return_value = expected
    result = p._normalize_tool_call(mock_call)
    if result != expected:
        assert False, f"got {result}"
    mock_call.model_dump.assert_called_once()


def test_normalize_tool_call_fallback_from_attributes():
    """Object with function attribute but no model_dump → reconstructed."""
    p = _make_provider()
    mock_fn = MagicMock()
    mock_fn.name = "my_tool"
    mock_fn.arguments = {"p1": "v1"}
    mock_call = MagicMock(spec=[])
    mock_call.function = mock_fn
    result = p._normalize_tool_call(mock_call)
    assert result == {"function": {"name": "my_tool", "arguments": {"p1": "v1"}}}, f"got {result}"


def test_normalize_tool_call_dict_fallback():
    """Object without model_dump/function attrs → dict(call) attempt, then raw."""
    p = _make_provider()
    # An object that can be iterated as (k,v) pairs
    obj = [("a", 1), ("b", 2)]
    result = p._normalize_tool_call(obj)
    assert result == {"a": 1, "b": 2}, f"got {result}"


def test_normalize_tool_call_raw_fallback():
    """Object where dict(call) fails → returned as-is."""
    p = _make_provider()
    result = p._normalize_tool_call("can't dict me")
    assert result == "can't dict me", f"got {result!r}"


# ── main ────────────────────────────────────────────────────────────────────
