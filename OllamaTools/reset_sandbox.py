"""
reset_sandbox.py — Wipe the sandbox workspace to a clean state.

Cancels all tracked jobs and clears the workspace directory, giving the AI
a fresh workspace without affecting installed system packages.
"""

import logging
from lib.global_registry import g_data
from OllamaTools import tool_success, tool_error


async def reset_sandbox() -> str:
    """
    Wipe the sandbox workspace directory to a clean state.

    Cancels all tracked jobs and clears /workspace.
    Installed system packages are NOT reset — only the workspace is wiped.
    Use this when the workspace needs a fresh start.

    Returns:
        tool_success confirming the reset, or tool_error on failure.
    """
    sandbox = g_data.get("sandbox_manager")
    if not sandbox:
        return tool_error("Sandbox is not available")
    try:
        result = await sandbox.reset()
        return tool_success(result)
    except Exception as e:
        logging.error(f"[reset_sandbox] Error: {e}", exc_info=True)
        return tool_error(str(e))


def get_tool():
    return {
        "type": "function",
        "safe": False,
        "categories": ["sandbox_dangerous"],
        "function": {
            "name": "reset_sandbox",
            "description": (
                "Wipe the sandbox workspace directory to a clean state. "
                "All running jobs are cancelled and tracked jobs cleared. "
                "Installed system packages are NOT affected. "
                "Use when the workspace needs a fresh start."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        "func": reset_sandbox,
    }
