"""
Tests for OllamaTools/__init__.py — tool_error, tool_success, and session enforcement.

Covers:
- tool_error produces valid JSON
- tool_error includes extra_fields in output
- tool_success produces valid JSON
- tool_success includes data and extra_fields in output
- tool_success raises TypeError on non-serializable data
- tool_error with empty error string works
- require_active_session enforces session requirement
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from OllamaTools import tool_error, tool_success
from OllamaTools import _require_active_session, require_active_session


# ── tool_error tests ──────────────────────────────────────────────────


def test_tool_error_produces_valid_json():
    """tool_error("msg") returns parseable JSON with success=False and error key."""
    raw = tool_error("something went wrong")
    result = json.loads(raw)

    assert result.get("success") is False, f"[FAIL] Expected success=False, got {result.get('success')!r}"
    assert result.get("error") == "something went wrong", f"[FAIL] Expected error='something went wrong', got {result.get('error')!r}"


def test_tool_error_includes_extra_fields():
    """tool_error("msg", query="test", code=42) includes extra fields in output."""
    raw = tool_error("search failed", query="test query", code=42)
    result = json.loads(raw)

    assert result.get("query") == "test query", f"[FAIL] Expected query='test query', got {result.get('query')!r}"
    assert result.get("code") == 42, f"[FAIL] Expected code=42, got {result.get('code')!r}"
    assert result.get("success") is False, f"[FAIL] Expected success=False, got {result.get('success')!r}"


def test_tool_error_empty_string():
    """tool_error("") works correctly with an empty error string."""
    raw = tool_error("")
    result = json.loads(raw)

    assert result.get("success") is False, f"[FAIL] Expected success=False, got {result.get('success')!r}"
    assert result.get("error") == "", f"[FAIL] Expected error='', got {result.get('error')!r}"


def test_tool_error_no_extra_fields():
    """tool_error("msg") with no extra fields only has success and error keys."""
    raw = tool_error("just an error")
    result = json.loads(raw)

    expected_keys = {"success", "error"}
    actual_keys = set(result.keys())
    assert actual_keys == expected_keys, f"[FAIL] Expected keys {expected_keys}, got {actual_keys}"


# ── tool_success tests ─────────────────────────────────────────────────


def test_tool_success_produces_valid_json():
    """tool_success({"key": "val"}) returns parseable JSON with success=True."""
    raw = tool_success({"key": "val"})
    result = json.loads(raw)

    assert result.get("success") is True, f"[FAIL] Expected success=True, got {result.get('success')!r}"
    assert result.get("data") == {"key": "val"}, f"[FAIL] Expected data={'key': 'val'}, got {result.get('data')!r}"


def test_tool_success_includes_extra_fields():
    """tool_success({"k": "v"}, query="test", status="ok") includes extra fields."""
    raw = tool_success({"k": "v"}, query="test query", status="ok")
    result = json.loads(raw)

    assert result.get("success") is True, f"[FAIL] Expected success=True, got {result.get('success')!r}"
    assert result.get("query") == "test query", f"[FAIL] Expected query='test query', got {result.get('query')!r}"
    assert result.get("status") == "ok", f"[FAIL] Expected status='ok', got {result.get('status')!r}"
    assert result.get("data") == {"k": "v"}, f"[FAIL] Expected data={'k': 'v'}, got {result.get('data')!r}"


def test_tool_success_nonserializable_data_raises():
    """tool_success with non-serializable data should raise TypeError."""
    class NonSerializable:
        pass

    with pytest.raises(TypeError):
        tool_success(NonSerializable())


def test_tool_success_with_list_data():
    """tool_success with list data produces correct JSON."""
    raw = tool_success([1, 2, 3])
    result = json.loads(raw)

    assert result.get("success") is True, f"[FAIL] Expected success=True, got {result.get('success')!r}"
    assert result.get("data") == [1, 2, 3], f"[FAIL] Expected data=[1, 2, 3], got {result.get('data')!r}"


def test_tool_success_with_none_data():
    """tool_success with None data produces correct JSON."""
    raw = tool_success(None)
    result = json.loads(raw)

    assert result.get("success") is True, f"[FAIL] Expected success=True, got {result.get('success')!r}"
    assert result.get("data") is None, f"[FAIL] Expected data=None, got {result.get('data')!r}"


def test_tool_success_no_extra_fields():
    """tool_success with no extra fields only has success and data keys."""
    raw = tool_success("result")
    result = json.loads(raw)

    expected_keys = {"success", "data"}
    actual_keys = set(result.keys())
    assert actual_keys == expected_keys, f"[FAIL] Expected keys {expected_keys}, got {actual_keys}"


# ── Session enforcement tests ──────────────────────────────────────────


class TestRequireActiveSession:
    """Tests for require_active_session() — session enforcement for edit tools."""

    def test_no_session_returns_error(self):
        """In a directory without .nami_change_session, returns error string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / ".nami_change_session"
            assert not marker.exists()

            # Patch _resolve_project_root to point to our temp dir
            import OllamaTools
            original = getattr(OllamaTools._require_active_session, "__wrapped__", None)
            session_mod = sys.modules.get("lib.services.session_manager")
            if session_mod:
                orig_resolve = session_mod._resolve_project_root
                session_mod._resolve_project_root = lambda: Path(tmpdir)

            try:
                result = require_active_session()
                assert result is not None, "Should return error when no session"
                parsed = json.loads(result)
                assert parsed["success"] is False
                assert "No active change session" in parsed["error"]
            finally:
                if session_mod:
                    session_mod._resolve_project_root = orig_resolve

    def test_active_session_returns_none(self):
        """When .nami_change_session exists, returns None (all good)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / ".nami_change_session"
            marker.write_text(json.dumps({
                "session_start": "2026-01-01T00:00:00Z",
                "safe_point": "abc123",
                "description": "test session",
            }))

            session_mod = sys.modules.get("lib.services.session_manager")
            if session_mod:
                orig_resolve = session_mod._resolve_project_root
                session_mod._resolve_project_root = lambda: Path(tmpdir)

            try:
                result = require_active_session()
                assert result is None, "Should return None when session is active"
            finally:
                if session_mod:
                    session_mod._resolve_project_root = orig_resolve

    def test_corrupted_marker_returns_error(self):
        """Corrupted marker treated as no session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / ".nami_change_session"
            marker.write_text("not valid json")

            session_mod = sys.modules.get("lib.services.session_manager")
            if session_mod:
                orig_resolve = session_mod._resolve_project_root
                session_mod._resolve_project_root = lambda: Path(tmpdir)

            try:
                result = require_active_session()
                assert result is not None, "Should return error for corrupted marker"
                parsed = json.loads(result)
                assert parsed["success"] is False
            finally:
                if session_mod:
                    session_mod._resolve_project_root = orig_resolve

    def test_require_active_session_returns_dict(self):
        """_require_active_session returns session data dict when active."""
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / ".nami_change_session"
            expected = {
                "session_start": "2026-01-01T00:00:00Z",
                "safe_point": "abc123def",
                "description": "my session",
            }
            marker.write_text(json.dumps(expected))

            session_mod = sys.modules.get("lib.services.session_manager")
            if session_mod:
                orig_resolve = session_mod._resolve_project_root
                session_mod._resolve_project_root = lambda: Path(tmpdir)

            try:
                data = _require_active_session()
                assert data is not None
                assert data["safe_point"] == "abc123def"
                assert data["description"] == "my session"
            finally:
                if session_mod:
                    session_mod._resolve_project_root = orig_resolve


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))