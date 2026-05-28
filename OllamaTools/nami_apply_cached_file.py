"""
nami_apply_cached_file — Restore a cached file into the working tree.

Copies a file from a past session's cache back into the project.
Only works within an active nami_begin_session for safety.
"""

import json
import logging

from lib.services.nami_session_cache import (
    apply_cached_file,
    _cache_root,
    _resolve_project_root,
    generate_session_id,
)
from OllamaTools import require_active_session


async def nami_apply_cached_file(
    session_id: str,
    file_path: str,
    reason: str = "",
) -> str:
    """
    Restore a file from a cached session into the working tree.

    Args:
        session_id: The session ID to restore from.
        file_path:  Relative path of the file within the project (e.g. 'lib/services/memory_service.py').
        reason:     Why this file is being restored (for logging/audit).

    Returns:
        JSON confirmation.
    """
    # Must be within an active session
    err = require_active_session()
    if err:
        return err

    project_root = _resolve_project_root()
    cache_root = _cache_root(project_root)
    session_dir = cache_root / session_id

    if not session_dir.exists():
        return json.dumps({
            "ok": False,
            "error": f"Session '{session_id}' not found in cache.",
        }, indent=2)

    success = apply_cached_file(session_dir, file_path, project_root)
    if not success:
        return json.dumps({
            "ok": False,
            "error": f"File '{file_path}' not found in session '{session_id}' cache.",
        }, indent=2)

    logging.info(
        f"[cache] Applied {file_path} from session {session_id}"
        + (f" (reason: {reason})" if reason else "")
    )

    return json.dumps({
        "ok": True,
        "session_id": session_id,
        "file_path": file_path,
        "reason": reason,
        "message": f"Restored {file_path} from session {session_id} into working tree.",
    }, indent=2)


def get_tool() -> list[dict]:
    return [
        {
            "type": "function",
            "safe": False,
            "categories": ["self_modification"],
            "function": {
                "name": "nami_apply_cached_file",
                "description": (
                    "Restore a file from a cached session into the working tree. "
                    "Only works within an active nami_begin_session. "
                    "Use this to recover a good approach from a previously failed session."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "The session ID to restore from (e.g. '2026-05-27T19-30-00_refactor-memory').",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Relative path of the file within the project (e.g. 'lib/services/memory_service.py').",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why this file is being restored (for logging/audit).",
                        },
                    },
                    "required": ["session_id", "file_path"],
                },
            },
            "func": nami_apply_cached_file,
        },
    ]
