"""
nami_reset_cache — Delete cached edit sessions.

Supports deleting all sessions, a specific session, or keeping
only failed sessions for debugging. dry_run previews without deleting.
"""

import json
import logging

from lib.services.nami_session_cache import reset_cache, _resolve_project_root


async def nami_reset_cache(
    session_id: str = "",
    keep_failed: bool = False,
    dry_run: bool = False,
) -> str:
    """
    Reset the session cache — delete past edit sessions.

    Args:
        session_id:  Specific session to delete. Empty = all sessions.
        keep_failed: If True, only delete passed/aborted sessions.
                     Failed sessions (with test results) are kept for debugging.
        dry_run:     Show what would be deleted without actually deleting.

    Returns:
        JSON with deleted/kept counts and session list.
    """
    project_root = _resolve_project_root()

    result = reset_cache(
        project_root=project_root,
        session_id=session_id,
        keep_failed=keep_failed,
        dry_run=dry_run,
    )

    if "error" in result:
        return json.dumps({"ok": False, "error": result["error"]}, indent=2)

    return json.dumps({
        "ok": True,
        "dry_run": dry_run,
        "deleted": result["deleted"],
        "kept": result.get("kept", 0),
        "sessions": result["sessions"],
    }, indent=2)


def get_tool() -> list[dict]:
    return [
        {
            "type": "function",
            "safe": False,
            "categories": ["self_modification"],
            "function": {
                "name": "nami_reset_cache",
                "description": (
                    "Delete cached edit sessions. Use this to clean up old "
                    "or unwanted session data. Failed sessions can be kept "
                    "for debugging with keep_failed=True."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Specific session to delete. Empty = all.",
                        },
                        "keep_failed": {
                            "type": "boolean",
                            "description": "Keep sessions that failed (for debugging). Default: false.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Preview only — don't actually delete. Default: false.",
                        },
                    },
                    "required": [],
                },
            },
            "func": nami_reset_cache,
        },
    ]
