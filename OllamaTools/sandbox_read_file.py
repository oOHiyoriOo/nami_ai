"""
sandbox_read_file.py — Read a file from the sandbox, optionally limiting to a line range.

Uses sed -n with shlex-quoted path to avoid shell escaping issues.
Returns file content with line numbers prepended.
"""

import logging
import shlex
from OllamaTools import _get_sandbox_or_error, tool_success, tool_error


async def sandbox_read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """
    Read a file from the sandbox filesystem.

    Reads the full file by default. Use start_line and end_line to read
    a specific range (line numbers are 1-based).

    Args:
        path:       Absolute path to the file in the sandbox.
        start_line: First line to read (1-based, default 1).
        end_line:   Last line to read (inclusive). If None, reads to end of file.

    Returns:
        tool_success with file content and line numbers, or tool_error on failure.
    """
    if start_line < 1:
        return tool_error(f"start_line must be >= 1, got {start_line}")

    if end_line is not None and end_line < start_line:
        return tool_error(
            f"end_line ({end_line}) must be >= start_line ({start_line})"
        )

    sandbox, err = _get_sandbox_or_error()
    if err:
        return err

    try:
        safe_path = shlex.quote(path)
        if end_line is not None:
            cmd = f"sed -n '{start_line},{end_line}p' {safe_path}"
        else:
            cmd = f"sed -n '{start_line},$p' {safe_path}"

        result = await sandbox.run(cmd)

        if result.get("status") == "done":
            output = result.get("output", "")
            # Prepend line numbers
            if output:
                lines = output.split("\n")
                # sed output may have a trailing newline
                if lines and lines[-1] == "":
                    lines = lines[:-1]
                numbered = "\n".join(
                    f"{start_line + i:>6}\t{line}"
                    for i, line in enumerate(lines)
                )
                return tool_success(numbered, path=path)
            return tool_success("", path=path)

        # Still running (auto-backgrounded — unlikely for cat/sed but handle it)
        return tool_success(result, path=path)

    except Exception as e:
        logging.error(f"[sandbox_read_file] Error: {e}", exc_info=True)
        return tool_error(str(e), path=path)


def get_tool() -> list[dict]:
    return [{
        "type": "function",
        "safe": True,
        "categories": ["sandbox"],
        "function": {
            "name": "sandbox_read_file",
            "description": (
                "Read a file from the sandbox filesystem. Returns the file content "
                "with line numbers prepended. Use start_line and end_line to read "
                "a specific range. Line numbers are 1-based."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to read (e.g. '/workspace/main.py')."
                    },
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "First line to read (1-based, default: 1)."
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (inclusive). If omitted, reads to end of file."
                    }
                },
                "required": ["path"]
            }
        },
        "func": sandbox_read_file,
    }]
