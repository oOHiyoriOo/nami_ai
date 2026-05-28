"""
nami_abort_session — Discard an active self-modification session.

Deletes the session marker and rolls back to the safe_point.
Use this when you want to abandon changes without running verification.
"""

import json
import logging
from pathlib import Path

from lib.services.nami_session_cache import _cache_root, finalize_cache
from lib.services.session_manager import (
    _resolve_project_root,
    delete_session_marker,
    read_session_marker,
    rollback_to_safe_point,
)


async def abort_session() -> str:
    """
    Abort the current self-modification session.

    Deletes the session marker and rolls back to the safe_point.
    Use this to discard changes without verification.

    Returns:
        JSON confirmation.
    """
    project_root = _resolve_project_root()

    existing = read_session_marker(project_root)
    if existing is None:
        return json.dumps({
            "ok": False,
            "error": "No active change session found.",
        }, indent=2)

    session_desc = existing.get("description", "unknown")
    session_id = existing.get("session_id", "")

    try:
        success = rollback_to_safe_point(project_root)
        delete_session_marker(project_root)

        # Finalize the cache
        if session_id:
            cache_dir = _cache_root(project_root) / session_id
            if cache_dir.exists():
                finalize_cache(cache_dir, "aborted")

        return json.dumps({
            "ok": success,
            "aborted": True,
            "rolled_back": success,
            "message": (
                f"Session '{session_desc}' aborted. "
                + ("Rolled back to safe_point." if success else "Rollback FAILED — manual intervention needed.")
            ),
        }, indent=2)

    except Exception as e:
        logging.error(f"[session] abort_session failed: {e}")
        return json.dumps({"ok": False, "error": str(e)}, indent=2)


def get_tool() -> list[dict]:
    return [
        {
            "type": "function",
            "safe": False,
            "categories": ["self_modification"],
            "function": {
                "name": "nami_abort_session",
                "description": (
                    "Abort the current self-modification session. "
                    "Rolls back to the safe_point and deletes the session marker. "
                    "Use this to discard changes without running verification."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            "func": abort_session,
        },
    ]
