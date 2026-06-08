"""
nami_inspect_cache — Browse past edit sessions stored in .nami_session_cache/.

Without a session_id, lists all cached sessions. With a session_id, shows
the full session.json including test results and changed files.
"""

import json
import logging

from lib.utils import resolve_project_root

from lib.services.nami_session_cache import (
    get_session_detail,
    list_sessions,
)


async def nami_inspect_cache(session_id: str = "", list_only: bool = False) -> str:
    """
    Inspect past edit sessions stored in the persistent session cache.

    Args:
        session_id: Specific session to inspect (empty = list all).
        list_only:   If True, return only session IDs and descriptions (compact).

    Returns:
        JSON with session info.
    """
    project_root = resolve_project_root()

    if session_id:
        detail = get_session_detail(project_root, session_id)
        if not detail:
            return json.dumps({
                "ok": False,
                "error": f"Session '{session_id}' not found in cache.",
            }, indent=2)
        return json.dumps({"ok": True, "session": detail}, indent=2)

    sessions = list_sessions(project_root)
    if list_only:
        compact = [
            {"session_id": s["session_id"], "description": s["description"]}
            for s in sessions
        ]
        return json.dumps({"ok": True, "sessions": compact, "total": len(compact)}, indent=2)

    return json.dumps({"ok": True, "sessions": sessions, "total": len(sessions)}, indent=2)


def get_tool() -> list[dict]:
    return [
        {
            "type": "function",
            "safe": False,
            "categories": ["self_modification"],
            "function": {
                "name": "nami_inspect_cache",
                "description": (
                    "Browse past edit sessions stored in .nami_session_cache/. "
                    "Without session_id: lists all sessions with status. "
                    "With session_id: shows full session.json including test results and changed files. "
                    "Use list_only=True for compact output."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Specific session to inspect. Empty = list all.",
                        },
                        "list_only": {
                            "type": "boolean",
                            "description": "Only show session IDs and descriptions (compact). Default: false.",
                        },
                    },
                    "required": [],
                },
            },
            "func": nami_inspect_cache,
        },
    ]
