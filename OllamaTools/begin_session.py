"""
nami_begin_session — Start a self-modification session.

Creates a git safe_point and session marker so that if the server crashes
during self-modification, it can auto-recover on restart.
"""

import json
import logging
from pathlib import Path

from lib.utils import resolve_project_root

from lib.services.nami_session_cache import generate_session_id, init_cache_dir
from lib.services.session_manager import (
    create_safe_point,
    create_session_marker,
    delete_session_marker,
    read_session_marker,
    SAFE_POINT_TAG,
)


async def begin_session(description: str = "") -> str:
    """
    Start a self-modification session. Creates a git safe_point and session marker.

    Args:
        description: What this session will change (e.g. "Refactor memory_service.py")

    Returns:
        JSON with session state.
    """
    project_root = resolve_project_root()

    # Refuse if an active session already exists
    existing = read_session_marker(project_root)
    if existing is not None:
        return json.dumps({
            "ok": False,
            "error": "A change session is already active. Complete or abort it first.",
            "active_session": {
                "started": existing.get("session_start"),
                "description": existing.get("description"),
            },
        }, indent=2)

    try:
        safe_point_sha = create_safe_point(project_root)
        if not safe_point_sha:
            return json.dumps({
                "ok": False,
                "error": "Could not determine HEAD SHA — is there at least one commit?",
            }, indent=2)

        desc = description or "Unnamed change session"
        session_id = generate_session_id(desc)

        create_session_marker(
            project_root=project_root,
            safe_point_sha=safe_point_sha,
            description=desc,
            session_id=session_id,
        )

        # Initialize the persistent session cache
        init_cache_dir(project_root, session_id, safe_point_sha, desc)

        return json.dumps({
            "ok": True,
            "session_id": session_id,
            "session_start": read_session_marker(project_root)["session_start"],
            "safe_point": safe_point_sha,
            "description": desc,
            "message": "Session started. Make your edits, then call nami_verify_session.",
        }, indent=2)

    except Exception as e:
        logging.error(f"[session] begin_session failed: {e}")
        return json.dumps({"ok": False, "error": str(e)}, indent=2)


def get_tool() -> list[dict]:
    return [
        {
            "type": "function",
            "safe": False,
            "categories": ["self_modification"],
            "function": {
                "name": "nami_begin_session",
                "description": (
                    "Start a self-modification session. Creates a git safe_point so "
                    "that if the server crashes, it auto-recovers on restart. "
                    "Call this BEFORE editing any source files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Brief description of what this session will change",
                        },
                    },
                    "required": [],
                },
            },
            "func": begin_session,
        },
    ]
