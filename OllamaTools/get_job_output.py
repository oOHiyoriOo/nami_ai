"""
get_job_output.py — Poll output from a background sandbox job.
"""

import logging
from OllamaTools import _get_sandbox_or_error, tool_success, tool_error


async def get_job_output(job_id: str) -> str:
    """
    Retrieve the current stdout/stderr of a background sandbox job.

    Args:
        job_id: Job ID returned by run_bash when a command was backgrounded.

    Returns:
        tool_success with output, status (running/done), and exit_code.
    """
    sandbox, err = _get_sandbox_or_error()
    if err:
        return err
    try:
        result = sandbox.get_output(job_id)
        return tool_success(result)
    except Exception as e:
        logging.error(f"[get_job_output] Error: {e}", exc_info=True)
        return tool_error(str(e), job_id=job_id)


def get_tool() -> list[dict]:
    return [{
        "type": "function",
        "safe": True,
        "categories": ["sandbox"],
        "function": {
            "name": "get_job_output",
            "description": (
                "Get current output and status of a background sandbox job. "
                "Use this to check on commands that were auto-backgrounded by run_bash."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The job_id returned by run_bash."
                    }
                },
                "required": ["job_id"]
            }
        },
        "func": get_job_output,
    }]
