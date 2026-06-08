"""
nami_read_cached_file — Read a cached file from a past session.

Reads the modified version of a file from a previous session cache.
Does NOT restore the file to the working tree. Use this to inspect
past changes before deciding whether to apply them.
"""

import json
import logging

from lib.utils import resolve_project_root

from lib.services.nami_session_cache import _cache_root
from lib.services.nami_session_io import read_cached_file


async def nami_read_cached_file(session_id: str, file_path: str) -> str:
    """
    Read a cached file from a past edit session.

    Args:
        session_id: The session ID (e.g. "2026-05-27T19-30-00_refactor-memory").
        file_path:  Relative path of the file within the project (e.g. "lib/services/memory_service.py").

    Returns:
        The file content as a string in the response. Does NOT restore.
    """
    project_root = resolve_project_root()
    cache_root = _cache_root(project_root)
    session_dir = cache_root / session_id

    if not session_dir.exists():
        return json.dumps({
            "ok": False,
            "error": f"Session '{session_id}' not found in cache.",
        }, indent=2)

    content = read_cached_file(session_dir, file_path)
    if content is None:
        return json.dumps({
            "ok": False,
            "error": f"File '{file_path}' not found in session '{session_id}' cache.",
        }, indent=2)

    return json.dumps({
        "ok": True,
        "session_id": session_id,
        "file_path": file_path,
        "content": content,
    }, indent=2)


def get_tool() -> list[dict]:
    return [
        {
            "type": "function",
            "safe": False,
            "categories": ["self_modification"],
            "function": {
                "name": "nami_read_cached_file",
                "description": (
                    "Read a cached file from a past edit session. "
                    "Use this to inspect changes from a previous session "
                    "without restoring them. Returns the file content."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "The session ID to read from (e.g. '2026-05-27T19-30-00_refactor-memory').",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Relative path of the file within the project (e.g. 'lib/services/memory_service.py').",
                        },
                    },
                    "required": ["session_id", "file_path"],
                },
            },
            "func": nami_read_cached_file,
        },
    ]
