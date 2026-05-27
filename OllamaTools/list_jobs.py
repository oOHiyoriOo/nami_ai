"""
list_jobs.py — List all tracked sandbox jobs (running and completed).
"""

import logging
from lib.global_registry import g_data
from OllamaTools import tool_success, tool_error


async def list_jobs() -> str:
    """
    List all sandbox jobs tracked in this session (running and completed).

    Returns:
        tool_success with a list of job summaries.
    """
    sandbox = g_data.get("sandbox_manager")
    if not sandbox:
        return tool_error("Sandbox is not available")
    try:
        jobs = sandbox.list_jobs()
        return tool_success(jobs if jobs else "No jobs tracked yet.")
    except Exception as e:
        logging.error(f"[list_jobs] Error: {e}", exc_info=True)
        return tool_error(str(e))


def get_tool():
    return {
        "type": "function",
        "safe": True,
        "categories": ["sandbox"],
        "function": {
            "name": "list_jobs",
            "description": (
                "List all sandbox background jobs in this session, including "
                "running and recently completed ones."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        "func": list_jobs,
    }
