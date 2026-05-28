"""
safe_edit_validator.py — Whitelist-based path validation for nami_edit_code.

Checks every file edit against safe_edit_paths.json before allowing writes.
Deny-list takes priority over allow-list. Unlisted paths are implicitly denied.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from pathlib import Path

_WHITELIST_PATH = Path(__file__).parent / "safe_edit_paths.json"
_REQUIRED_VERSION = 1

# Cached whitelist data
_whitelist: dict | None = None


def _resolve_project_root() -> Path:
    """Find the project root containing api_server.py or lib/services."""
    for anchor in [Path("/workspace/project/nami_ai"), Path.cwd()]:
        if (anchor / "api_server.py").exists():
            return anchor
    current = Path.cwd()
    for _ in range(5):
        if (current / "api_server.py").exists():
            return current
        current = current.parent
    return Path.cwd()


def _load_whitelist() -> dict:
    """Load and validate the whitelist. Cached after first load."""
    global _whitelist
    if _whitelist is not None:
        return _whitelist

    if not _WHITELIST_PATH.exists():
        logging.error(
            "[safe_edit] Whitelist file not found: %s — denying all edits",
            _WHITELIST_PATH,
        )
        _whitelist = {"version": 1, "allow": [], "deny": []}
        return _whitelist

    try:
        data = json.loads(_WHITELIST_PATH.read_text())
    except json.JSONDecodeError as e:
        logging.critical("[safe_edit] Invalid whitelist JSON — denying all edits: %s", e)
        _whitelist = {"version": 1, "allow": [], "deny": []}
        return _whitelist

    version = data.get("version")
    if version != _REQUIRED_VERSION:
        logging.error(
            "[safe_edit] Whitelist version %r != required %r — denying all edits",
            version,
            _REQUIRED_VERSION,
        )
        _whitelist = {"version": 1, "allow": [], "deny": []}
        return _whitelist

    if "allow" not in data or "deny" not in data:
        logging.critical("[safe_edit] Whitelist missing allow/deny keys — denying all edits")
        _whitelist = {"version": 1, "allow": [], "deny": []}
        return _whitelist

    _whitelist = data
    logging.debug("[safe_edit] Whitelist loaded: %d allow, %d deny",
                   len(data["allow"]), len(data["deny"]))
    return _whitelist


def _clear_cache() -> None:
    """Clear the cached whitelist (for testing)."""
    global _whitelist
    _whitelist = None


def _format_allowed_paths(whitelist: dict) -> str:
    """Format allowed paths into a human-readable list for error messages."""
    lines = []
    for entry in whitelist.get("allow", []):
        lines.append(f"  • {entry['pattern']} — {entry['reason']}")
    if not lines:
        return "  (no paths are currently allowed)"
    return "\n".join(lines)


def validate_edit_path(file_path: str) -> tuple[bool, str, str | None]:
    """
    Check if file_path is allowed for editing.

    Args:
        file_path: Absolute or relative path to the file being edited.

    Returns:
        (allowed: bool, reason: str, reload_event: str | None)

        - allowed: True if the file can be edited
        - reason: Human-readable explanation (grant or denial reason)
        - reload_event: Suggested reload event name, or None
    """
    whitelist = _load_whitelist()

    # Resolve to an absolute path first, then make relative to project root
    abs_path = Path(file_path).resolve()
    project_root = _resolve_project_root()

    try:
        rel_path = abs_path.relative_to(project_root)
    except ValueError:
        return False, f"Path '{file_path}' is outside the project root", None

    # ── Deny list takes priority ──────────────────────────────────────
    for entry in whitelist.get("deny", []):
        if fnmatch.fnmatch(str(rel_path), entry["pattern"]):
            return False, entry["reason"], None

    # ── Check allow list ──────────────────────────────────────────────
    for entry in whitelist.get("allow", []):
        if fnmatch.fnmatch(str(rel_path), entry["pattern"]):
            return True, entry["reason"], entry.get("reload")

    # ── Implicit deny for unlisted paths ──────────────────────────────
    allowed_list = _format_allowed_paths(whitelist)
    reason = (
        f"File '{rel_path}' is not in the edit whitelist. "
        f"Allowed paths include:\n{allowed_list}"
    )
    return False, reason, None


def is_edit_allowed(file_path: str) -> bool:
    """Convenience: return True if the path can be edited."""
    allowed, _, _ = validate_edit_path(file_path)
    return allowed
