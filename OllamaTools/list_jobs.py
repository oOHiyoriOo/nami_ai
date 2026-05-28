"""
list_jobs.py — List all tracked sandbox jobs (running and completed).
"""

import logging
from OllamaTools import _get_sandbox_or_error, tool_success, tool_error


async def list_jobs() -> str:
    """
    List all sandbox jobs tracked in this session (running and completed).

    Returns:
        tool_success with a list of job summaries.
    """
    sandbox, err = _get_sandbox_or_error()
    if err:
        return err
    try:
        jobs = sandbox.list_jobs()
        return tool_success(jobs if jobs else "No jobs tracked yet.")
    except Exception as e:
        logging.error(f"[list_jobs] Error: {e}", exc_info=True)
        return tool_error(str(e))


def get_tool() -> list[dict]:
    return [{
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
    }]
