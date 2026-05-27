"""
run_bash.py — Execute a shell command in the sandbox container.

Commands finishing within the foreground timeout return output immediately.
Longer-running commands are auto-backgrounded and return a job_id for polling.
"""

import logging
from lib.global_registry import g_data
from OllamaTools import tool_success, tool_error


async def run_bash(command: str) -> str:
    """
    Run a shell command in the isolated sandbox container.

    Short commands return full output. Long-running commands are
    automatically moved to the background and a job_id is returned.

    Args:
        command: Shell command to execute (bash syntax).

    Returns:
        tool_success with output (done) or job_id (still running).
    """
    sandbox = g_data.get("sandbox_manager")
    if not sandbox:
        return tool_error("Sandbox is not available")
    try:
        result = await sandbox.run(command)
        return tool_success(result, command=command)
    except Exception as e:
        logging.error(f"[run_bash] Error: {e}", exc_info=True)
        return tool_error(str(e), command=command)


def get_tool():
    return {
        "type": "function",
        "safe": False,
        "categories": ["sandbox_dangerous"],
        "function": {
            "name": "run_bash",
            "description": (
                "Execute a shell command in an isolated sandbox container. "
                "Returns full output for short commands. If the command is still "
                "running after ~15 seconds it is auto-backgrounded and a job_id "
                "is returned — use get_job_output to poll for results later."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to run (e.g. 'ls -la', 'python3 script.py')."
                    }
                },
                "required": ["command"]
            }
        },
        "func": run_bash,
    }
