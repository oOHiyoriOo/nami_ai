"""
sandbox_write_file.py — Write content to a file in the sandbox without shell escaping.

Uses base64 encoding to pass content through the shell safely. The AI passes
content as a plain tool argument — no heredocs, no escaping, no metacharacter
issues. The tool handles all encoding internally.
"""

import base64
import logging
import os
import shlex
from OllamaTools import _get_sandbox_or_error, tool_success, tool_error


async def sandbox_write_file(path: str, content: str) -> str:
    """
    Write content to a file in the sandbox. Overwrites if the file exists.

    Content is base64-encoded before passing through the shell, eliminating
    all escaping issues — backticks, dollar signs, quotes, and other
    metacharacters are handled transparently.

    Args:
        path:    Absolute path to the file to write (e.g. '/workspace/config.json').
        content: The text content to write to the file.

    Returns:
        tool_success confirming the write, or tool_error on failure.
    """
    sandbox, err = _get_sandbox_or_error()
    if err:
        return err

    try:
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        safe_path = shlex.quote(path)
        safe_parent = shlex.quote(os.path.dirname(path))
        cmd = f"mkdir -p {safe_parent} && echo {shlex.quote(encoded)} | base64 -d > {safe_path}"
        result = await sandbox.run(cmd)

        if result.get("status") == "done":
            if result.get("exit_code") == 0:
                return tool_success(
                    f"Wrote {len(content)} bytes to {path}",
                    path=path,
                    bytes_written=len(content),
                )
            return tool_error(
                f"Write failed (exit code {result.get('exit_code')}): {result.get('output', '')}",
                path=path,
            )

        # Auto-backgrounded — unlikely but handle it
        return tool_success(result, path=path)

    except Exception as e:
        logging.error(f"[sandbox_write_file] Error: {e}", exc_info=True)
        return tool_error(str(e), path=path)


def get_tool() -> list[dict]:
    return [{
        "type": "function",
        "safe": False,
        "categories": ["sandbox_dangerous"],
        "function": {
            "name": "sandbox_write_file",
            "description": (
                "Write content to a file in the sandbox. Overwrites if the file "
                "already exists. Content is passed as a plain argument — no shell "
                "escaping needed. Creates parent directories automatically if "
                "needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to write (e.g. '/workspace/config.json')."
                    },
                    "content": {
                        "type": "string",
                        "description": "The text content to write to the file."
                    }
                },
                "required": ["path", "content"]
            }
        },
        "func": sandbox_write_file,
    }]
