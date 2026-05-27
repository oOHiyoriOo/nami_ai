"""
sandbox_list_dir.py — List directory contents in the sandbox with structured output.

Uses ls -la with a stable time format, then parses the output into a structured
list of file entries. Far more usable than raw ls output for an AI consumer.
"""

import logging
import shlex
from lib.global_registry import g_data
from OllamaTools import tool_success, tool_error


async def sandbox_list_dir(path: str = "/workspace") -> str:
    """
    List files and directories in a sandbox path.

    Returns a structured list of entries with name, size, type, permissions,
    and modification time. Much easier to consume than raw ls output.

    Args:
        path: Absolute path to the directory to list (default: '/workspace').

    Returns:
        tool_success with structured file list, or tool_error on failure.
    """
    sandbox = g_data.get("sandbox_manager")
    if not sandbox:
        return tool_error("Sandbox is not available")

    try:
        safe_path = shlex.quote(path)
        cmd = f"ls -la --time-style=long-iso {safe_path}"
        result = await sandbox.run(cmd)

        if result.get("status") != "done":
            return tool_success(result, path=path)

        output = result.get("output", "")
        if result.get("exit_code") != 0:
            return tool_error(
                f"ls failed (exit code {result.get('exit_code')}): {output}",
                path=path,
            )

        entries = _parse_ls_output(output)
        return tool_success(entries, path=path, count=len(entries))

    except Exception as e:
        logging.error(f"[sandbox_list_dir] Error: {e}", exc_info=True)
        return tool_error(str(e), path=path)


def _parse_ls_output(output: str) -> list[dict]:
    """
    Parse 'ls -la --time-style=long-iso' output into structured entries.

    Handles the standard ls -la format:
      drwxr-xr-x 2 root root 4096 2026-05-08 12:00 dirname
      -rw-r--r-- 1 root root  123 2026-05-08 12:00 filename
      lrwxrwxrwx 1 root root   10 2026-05-08 12:00 linkname -> target

    Skips the 'total N' header line and '.' / '..' entries.
    """
    entries = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("total "):
            continue

        parts = line.split(None, 7)
        if len(parts) < 8:
            continue

        perms = parts[0]
        links = parts[1]
        owner = parts[2]
        group = parts[3]
        size_str = parts[4]
        date_str = parts[5]
        time_str = parts[6]
        name_and_target = parts[7]

        # Handle symlink: name -> target
        if " -> " in name_and_target:
            name, target = name_and_target.split(" -> ", 1)
        else:
            name = name_and_target
            target = None

        entry_type = "directory" if perms.startswith("d") else \
                     "symlink" if perms.startswith("l") else "file"

        try:
            size = int(size_str)
        except ValueError:
            size = 0

        # Skip . and .. entries
        if name in (".", ".."):
            continue

        entries.append({
            "name": name,
            "type": entry_type,
            "size": size,
            "permissions": perms,
            "owner": owner,
            "group": group,
            "modified": f"{date_str} {time_str}",
            **({"target": target} if target else {}),
        })

    return entries


def get_tool():
    return {
        "type": "function",
        "safe": True,
        "categories": ["sandbox"],
        "function": {
            "name": "sandbox_list_dir",
            "description": (
                "List files and directories in a sandbox path. Returns a "
                "structured list with name, type, size, permissions, owner, "
                "and modification time for each entry."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the directory to list (default: '/workspace')."
                    }
                },
                "required": []
            }
        },
        "func": sandbox_list_dir,
    }
