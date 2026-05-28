"""
kill_job.py — Terminate a running background sandbox job.
"""

import logging
from OllamaTools import _get_sandbox_or_error, tool_success, tool_error


async def kill_job(job_id: str) -> str:
    """
    Terminate a background sandbox job.

    Args:
        job_id: The job_id to terminate.

    Returns:
        tool_success confirming the job was killed or was already done.
    """
    sandbox, err = _get_sandbox_or_error()
    if err:
        return err
    try:
        result = sandbox.kill_job(job_id)
        return tool_success(result)
    except Exception as e:
        logging.error(f"[kill_job] Error: {e}", exc_info=True)
        return tool_error(str(e), job_id=job_id)


def get_tool() -> list[dict]:
    return [{
        "type": "function",
        "safe": False,
        "categories": ["sandbox_dangerous"],
        "function": {
            "name": "kill_job",
            "description": "Terminate a running background sandbox job by its job_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The job_id to terminate."
                    }
                },
                "required": ["job_id"]
            }
        },
        "func": kill_job,
    }]
