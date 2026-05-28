"""
OllamaTools - Tool implementations for AI assistants.
"""
import json
from pathlib import Path
from typing import Any

from lib.global_registry import g_data


def _get_sandbox_or_error():
    """Return (sandbox_manager, None) or (None, tool_error) if unavailable."""
    sandbox = g_data.get("sandbox_manager")
    if not sandbox:
        return None, tool_error("Sandbox is not available")
    return sandbox, None


def tool_error(error: str, **extra_fields) -> str:
    """
    Create a standardized error response for tools.
    
    Args:
        error: Error message
        **extra_fields: Additional context fields (e.g., url, query)
    
    Returns:
        JSON string with consistent error format
    """
    result = {"success": False, "error": error, **extra_fields}
    return json.dumps(result)


def tool_success(data: Any, **extra_fields) -> str:
    """
    Create a standardized success response for tools.
    
    Args:
        data: The result data
        **extra_fields: Additional context fields
    
    Returns:
        JSON string with consistent success format
    """
    result = {"success": True, "data": data, **extra_fields}
    return json.dumps(result)


def _require_active_session() -> dict | None:
    """
    Check that a self-modification session is active.

    Reads .nami_change_session from the project root. Returns the session
    data dict if a session is active, or None if no session exists.

    Callers should convert None to a tool_error() response.

    Returns:
        Session data dict (safe_point, session_start, description) or None.
    """
    from lib.services.session_manager import _resolve_project_root

    project_root = _resolve_project_root()
    session_file = project_root / ".nami_change_session"
    if not session_file.exists():
        return None
    try:
        return json.loads(session_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def require_active_session() -> str | None:
    """
    Session enforcement for edit tools.

    Returns a tool_error() string if no active session, or None if all good.
    Use at the top of any self-modification tool:

        err = require_active_session()
        if err:
            return err
    """
    session = _require_active_session()
    if session is None:
        return tool_error(
            "No active change session. Call nami_begin_session first.\n\n"
            "nami_begin_session creates a git safety point that allows "
            "automatic rollback if something goes wrong."
        )
    return None