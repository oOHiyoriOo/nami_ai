"""
Tests for OllamaTools/nami_edit_code.py — safe code editing tool.

Covers:
- get_tool() structure validation
- Successful edit with unique old_str
- Non-unique old_str error (multi-match)
- old_str not found error
- File not found error
- Path outside project root error
- Path denied by whitelist error
- Backup creation
- Reload event publishing
- No active session → error
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from OllamaTools.nami_edit_code import nami_edit_code, get_tool


def _parse(raw: str) -> dict:
    return json.loads(raw)


# ── get_tool() tests ─────────────────────────────────────────────────


def test_get_tool_structure():
    tool = get_tool()[0]
    assert tool.get("type") == "function"
    assert tool.get("function")
    fn = tool["function"]
    assert fn.get("name") == "nami_edit_code"
    assert "file_path" in fn.get("parameters", {}).get("properties", {})
    assert "old_str" in fn.get("parameters", {}).get("properties", {})
    assert "new_str" in fn.get("parameters", {}).get("properties", {})
    required = fn.get("parameters", {}).get("required", [])
    assert "file_path" in required
    assert "old_str" in required
    assert "new_str" in required


def test_get_tool_safe_is_false():
    tool = get_tool()[0]
    assert tool.get("safe") is False


def test_get_tool_categories():
    tool = get_tool()[0]
    assert "self_modification" in tool.get("categories", [])


# ── nami_edit_code() tests ───────────────────────────────────────────


def test_no_active_session():
    """Require active session → error."""
    with (
        patch("OllamaTools.nami_edit_code.require_active_session") as mock_session,
    ):
        mock_session.return_value = json.dumps({"success": False, "error": "No active change session."})

        raw = asyncio.run(nami_edit_code(
            file_path="/workspace/project/nami_ai/OllamaTools/dummy.py",
            old_str="hello",
            new_str="world",
        ))
    r = _parse(raw)
    assert r.get("success") is False
    assert "No active change session" in r.get("error", "")


def test_path_denied_by_whitelist(tmp_path):
    """Path not in whitelist → error."""
    test_file = tmp_path / "denied.py"
    test_file.write_text("hello world")
    test_file_path = str(test_file)

    with (
        patch("OllamaTools.nami_edit_code.require_active_session", return_value=None),
        patch("OllamaTools.nami_edit_code.validate_edit_path") as mock_validate,
    ):
        mock_validate.return_value = (False, "Not in whitelist", None)

        raw = asyncio.run(nami_edit_code(
            file_path=test_file_path,
            old_str="hello",
            new_str="world",
        ))
    r = _parse(raw)
    assert r.get("success") is False
    assert "Not in whitelist" in r.get("error", "")


def test_old_str_not_found(tmp_path):
    """old_str not in file → error."""
    test_file = tmp_path / "test.py"
    test_file.write_text("hello world")
    test_file_path = str(test_file)

    with (
        patch("OllamaTools.nami_edit_code.require_active_session", return_value=None),
        patch("OllamaTools.nami_edit_code.validate_edit_path") as mock_validate,
        patch("OllamaTools.nami_edit_code._PROJECT_ROOT", tmp_path),
    ):
        mock_validate.return_value = (True, "allowed", "system.reload_tools")

        raw = asyncio.run(nami_edit_code(
            file_path=test_file_path,
            old_str="nonexistent",
            new_str="replacement",
        ))
    r = _parse(raw)
    assert r.get("success") is False
    assert "not found" in r.get("error", "")


def test_old_str_not_unique(tmp_path):
    """old_str found multiple times → error."""
    test_file = tmp_path / "test.py"
    test_file.write_text("duplicate\nduplicate\nunique")
    test_file_path = str(test_file)

    with (
        patch("OllamaTools.nami_edit_code.require_active_session", return_value=None),
        patch("OllamaTools.nami_edit_code.validate_edit_path") as mock_validate,
        patch("OllamaTools.nami_edit_code._PROJECT_ROOT", tmp_path),
    ):
        mock_validate.return_value = (True, "allowed", "system.reload_tools")

        raw = asyncio.run(nami_edit_code(
            file_path=test_file_path,
            old_str="duplicate",
            new_str="unique",
        ))
    r = _parse(raw)
    assert r.get("success") is False
    assert "2 times" in r.get("error", "")


def test_successful_edit(tmp_path):
    """Happy path: unique old_str → success with diff and backup."""
    test_file = tmp_path / "test.py"
    test_file.write_text("line1\nline2 old line3\nline4")
    test_file_path = str(test_file)

    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()
    mock_db = MagicMock()
    mock_driver = MagicMock()
    mock_session = AsyncMock()
    mock_driver.session.return_value.__aenter__.return_value = mock_session
    mock_db.get_driver.return_value = mock_driver

    with (
        patch("OllamaTools.nami_edit_code.require_active_session", return_value=None),
        patch("OllamaTools.nami_edit_code.validate_edit_path") as mock_validate,
        patch("OllamaTools.nami_edit_code._PROJECT_ROOT", tmp_path),
        patch("OllamaTools.nami_edit_code._BACKUP_DIR", tmp_path / ".nami_backups"),
        patch("OllamaTools.nami_edit_code.g_data") as mock_g_data,
    ):
        mock_validate.return_value = (True, "allowed", "system.reload_tools")
        mock_g_data.get.side_effect = lambda key: {
            "event_bus": mock_event_bus,
            "memory_db": mock_db,
        }.get(key)

        raw = asyncio.run(nami_edit_code(
            file_path=test_file_path,
            old_str="line2 old line3",
            new_str="line2 NEW line3",
            description="test edit",
            auto_reload=True,
        ))

    r = _parse(raw)
    assert r.get("success") is True, f"Expected success, got: {r}"
    assert r.get("replacements") == 1
    assert "diff" in r
    assert "backup" in r

    # Verify file was written
    content = test_file.read_text()
    assert "line2 NEW line3" in content
    assert "line2 old line3" not in content

    # Verify backup was created
    backups = list((tmp_path / ".nami_backups").iterdir())
    assert len(backups) == 1
    assert backups[0].name.startswith("test.py.")
    assert backups[0].name.endswith(".bak")
    backup_content = backups[0].read_text()
    assert "line2 old line3" in backup_content

    # Verify reload event was published
    mock_event_bus.publish.assert_called_once()


def test_auto_reload_disabled(tmp_path):
    """auto_reload=False → no event published."""
    test_file = tmp_path / "test.py"
    test_file.write_text("do not reload")
    test_file_path = str(test_file)

    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()
    mock_db = MagicMock()
    mock_driver = MagicMock()
    mock_session = AsyncMock()
    mock_driver.session.return_value.__aenter__.return_value = mock_session
    mock_db.get_driver.return_value = mock_driver

    with (
        patch("OllamaTools.nami_edit_code.require_active_session", return_value=None),
        patch("OllamaTools.nami_edit_code.validate_edit_path") as mock_validate,
        patch("OllamaTools.nami_edit_code._PROJECT_ROOT", tmp_path),
        patch("OllamaTools.nami_edit_code._BACKUP_DIR", tmp_path / ".nami_backups"),
        patch("OllamaTools.nami_edit_code.g_data") as mock_g_data,
    ):
        mock_validate.return_value = (True, "allowed", "system.reload_tools")
        mock_g_data.get.side_effect = lambda key: {
            "event_bus": mock_event_bus,
            "memory_db": mock_db,
        }.get(key)

        raw = asyncio.run(nami_edit_code(
            file_path=test_file_path,
            old_str="do not reload",
            new_str="reloaded anyway",
            auto_reload=False,
        ))

    r = _parse(raw)
    assert r.get("success") is True
    mock_event_bus.publish.assert_not_called()


def test_file_not_found(tmp_path):
    """Non-existent file → error."""
    test_file_path = str(tmp_path / "nonexistent.py")

    with (
        patch("OllamaTools.nami_edit_code.require_active_session", return_value=None),
        patch("OllamaTools.nami_edit_code.validate_edit_path") as mock_validate,
        patch("OllamaTools.nami_edit_code._PROJECT_ROOT", tmp_path),
    ):
        mock_validate.return_value = (True, "allowed", "system.reload_tools")

        raw = asyncio.run(nami_edit_code(
            file_path=test_file_path,
            old_str="anything",
            new_str="anything",
        ))
    r = _parse(raw)
    assert r.get("success") is False
    assert "not found" in r.get("error", "")


def test_path_outside_project_root(tmp_path):
    """Path resolved outside project root → error."""
    with (
        patch("OllamaTools.nami_edit_code.require_active_session", return_value=None),
        patch("OllamaTools.nami_edit_code.validate_edit_path") as mock_validate,
    ):
        mock_validate.return_value = (True, "allowed", "system.reload_tools")

        raw = asyncio.run(nami_edit_code(
            file_path="/etc/passwd",
            old_str="root",
            new_str="nobody",
        ))
    r = _parse(raw)
    assert r.get("success") is False
    assert "outside" in r.get("error", "")
