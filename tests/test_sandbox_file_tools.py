"""
Tests for sandbox file operation tools: sandbox_read_file, sandbox_write_file, sandbox_list_dir.

Covers:
- Sandbox-unavailable error path (all three tools)
- Successful delegation to SandboxManager
- Exception wrapping
- get_tool() schema validation
- sandbox_read_file: full file, line range, empty file
- sandbox_write_file: base64 encoding, write success, write failure
- sandbox_list_dir: ls output parsing, empty directory, symlink handling
"""

import asyncio
import base64
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from OllamaTools.sandbox_read_file import sandbox_read_file, get_tool as read_file_tool
from OllamaTools.sandbox_write_file import sandbox_write_file, get_tool as write_file_tool
from OllamaTools.sandbox_list_dir import sandbox_list_dir, get_tool as list_dir_tool, _parse_ls_output


def _parse(raw: str) -> dict:
    return json.loads(raw)


def _make_sandbox():
    """Create a MagicMock sandbox_manager with async run()."""
    sandbox = MagicMock()
    sandbox.run = AsyncMock()
    return sandbox


# ══════════════════════════════════════════════════════════════════════
# sandbox_read_file tests
# ══════════════════════════════════════════════════════════════════════

def test_read_file_sandbox_not_available():
    """g_data has no sandbox_manager → tool_error."""
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = None
        raw = asyncio.run(sandbox_read_file("/workspace/test.py"))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("Sandbox is not available" not in r.get("error", "")), "Test failed"


def test_read_file_full():
    """Read entire file → returns numbered content."""
    sandbox = _make_sandbox()
    sandbox.run.return_value = {
        "status": "done",
        "exit_code": 0,
        "output": "line1\nline2\nline3\n",
    }
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_read_file("/workspace/test.py"))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    data = r.get("data", "")
    assert not ("line1" not in data or "line2" not in data), "Test failed"
    # Check line numbers are prepended
    assert not ("\t" not in data), "Test failed"


def test_read_file_line_range():
    """Read specific line range → uses sed with start,end range."""
    sandbox = _make_sandbox()
    sandbox.run.return_value = {
        "status": "done",
        "exit_code": 0,
        "output": "line10\nline11\nline12\n",
    }
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_read_file("/workspace/large.py", start_line=10, end_line=12))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    # Verify sed was called with correct range
    call_arg = sandbox.run.call_args[0][0]
    assert not ("10,12p" not in call_arg), "Test failed"


def test_read_file_no_end_line():
    """Read from start_line to end of file → uses start,$p."""
    sandbox = _make_sandbox()
    sandbox.run.return_value = {
        "status": "done",
        "exit_code": 0,
        "output": "line5\nline6\nline7\n",
    }
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_read_file("/workspace/test.py", start_line=5))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    call_arg = sandbox.run.call_args[0][0]
    assert not ("5,$p" not in call_arg), "Test failed"


def test_read_file_empty():
    """Empty file → returns empty string."""
    sandbox = _make_sandbox()
    sandbox.run.return_value = {
        "status": "done",
        "exit_code": 0,
        "output": "",
    }
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_read_file("/workspace/empty.txt"))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r.get("data") != ""), "Test failed"


def test_read_file_exception():
    """Sandbox.run raises → tool_error."""
    sandbox = _make_sandbox()
    sandbox.run.side_effect = RuntimeError("SSH connection lost")
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_read_file("/workspace/test.py"))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not (r.get("error") not in ("SSH connection lost", "disk full", "connection timeout")), "Test failed"


def test_read_file_start_line_zero():
    """start_line=0 → tool_error (sed uses 1-based addressing)."""
    sandbox = _make_sandbox()
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_read_file("/workspace/test.py", start_line=0))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("start_line must be >= 1" not in r.get("error", "")), "Test failed"


def test_read_file_start_line_negative():
    """start_line=-5 → tool_error."""
    sandbox = _make_sandbox()
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_read_file("/workspace/test.py", start_line=-5))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("start_line must be >= 1" not in r.get("error", "")), "Test failed"


def test_read_file_end_line_before_start():
    """end_line < start_line → tool_error."""
    sandbox = _make_sandbox()
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_read_file("/workspace/test.py", start_line=10, end_line=5))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("end_line (5) must be >= start_line (10)" not in r.get("error", "")), "Test failed"


def test_read_file_get_tool():
    """get_tool() returns valid schema for sandbox_read_file."""
    tool = read_file_tool()[0]
    fail = []
    if tool.get("type") != "function":
        fail.append(f"type={tool.get('type')}")
    fn = tool.get("function", {})
    if fn.get("name") != "sandbox_read_file":
        fail.append(f"name={fn.get('name')}")
    if tool.get("safe") is not True:
        fail.append(f"safe={tool.get('safe')}")
    if "sandbox" not in tool.get("categories", []):
        fail.append(f"categories={tool.get('categories')}")
    props = fn.get("parameters", {}).get("properties", {})
    if "path" not in props:
        fail.append("missing path property")
    if "start_line" not in props:
        fail.append("missing start_line property")
    start_line_schema = props.get("start_line", {})
    if start_line_schema.get("minimum") != 1:
        fail.append(f"start_line minimum should be 1, got {start_line_schema.get('minimum')}")
    if "end_line" not in props:
        fail.append("missing end_line property")
    if "path" not in fn.get("parameters", {}).get("required", []):
        fail.append("path not required")
    if not callable(tool.get("func")):
        fail.append("func not callable")
    assert not (fail), "Test failed"


# ══════════════════════════════════════════════════════════════════════
# sandbox_write_file tests
# ══════════════════════════════════════════════════════════════════════

def test_write_file_sandbox_not_available():
    """g_data has no sandbox_manager → tool_error."""
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = None
        raw = asyncio.run(sandbox_write_file("/workspace/test.txt", "hello"))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("Sandbox is not available" not in r.get("error", "")), "Test failed"


def test_write_file_success():
    """Write succeeds → tool_success with bytes_written."""
    sandbox = _make_sandbox()
    sandbox.run.return_value = {
        "status": "done",
        "exit_code": 0,
        "output": "",
    }
    content = "hello world\nline 2"
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_write_file("/workspace/test.txt", content))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r.get("bytes_written") != len(content)), "Test failed"
    # Verify base64 encoding was used (no raw content in shell command)
    call_arg = sandbox.run.call_args[0][0]
    assert not ("base64 -d" not in call_arg), "Test failed"
    # Verify raw content is NOT in the command
    assert not ("hello world" in call_arg), "Test failed"
    # Verify the encoded content decodes back correctly
    parts = call_arg.split(" | ")
    # cmd is now: "mkdir -p <parent> && echo <base64> | base64 -d > <path>"
    encoded_part = parts[0].split("&& echo ")[-1]
    decoded = base64.b64decode(encoded_part.encode("ascii")).decode("utf-8")
    assert not (decoded != content), "Test failed"


def test_write_file_special_characters():
    """Content with $, backticks, quotes is base64-encoded safely."""
    sandbox = _make_sandbox()
    sandbox.run.return_value = {
        "status": "done",
        "exit_code": 0,
        "output": "",
    }
    content = 'echo "$HOME" && ls `pwd`; cat <<EOF\nline\nEOF'
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_write_file("/workspace/script.sh", content))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    call_arg = sandbox.run.call_args[0][0]
    # cmd is now: "mkdir -p <parent> && echo <base64> | base64 -d > <path>"
    encoded_part = call_arg.split(" | ")[0].split("&& echo ")[-1]
    decoded = base64.b64decode(encoded_part.encode("ascii")).decode("utf-8")
    assert not (decoded != content), "Test failed"


def test_write_file_failure():
    """Write fails (e.g. permission denied) → tool_error."""
    sandbox = _make_sandbox()
    sandbox.run.return_value = {
        "status": "done",
        "exit_code": 1,
        "output": "bash: /protected/file.txt: Permission denied\n",
    }
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_write_file("/protected/file.txt", "data"))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("Permission denied" not in r.get("error", "")), "Test failed"


def test_write_file_exception():
    """Sandbox.run raises → tool_error."""
    sandbox = _make_sandbox()
    sandbox.run.side_effect = RuntimeError("disk full")
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_write_file("/workspace/test.txt", "data"))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not (r.get("error") not in ("SSH connection lost", "disk full", "connection timeout")), "Test failed"


def test_write_file_get_tool():
    """get_tool() returns valid schema for sandbox_write_file."""
    tool = write_file_tool()[0]
    fail = []
    if tool.get("type") != "function":
        fail.append(f"type={tool.get('type')}")
    fn = tool.get("function", {})
    if fn.get("name") != "sandbox_write_file":
        fail.append(f"name={fn.get('name')}")
    if tool.get("safe") is not False:
        fail.append(f"safe={tool.get('safe')}")
    if "sandbox_dangerous" not in tool.get("categories", []):
        fail.append(f"categories={tool.get('categories')}")
    props = fn.get("parameters", {}).get("properties", {})
    if "path" not in props:
        fail.append("missing path property")
    if "content" not in props:
        fail.append("missing content property")
    required = fn.get("parameters", {}).get("required", [])
    if "path" not in required:
        fail.append("path not required")
    if "content" not in required:
        fail.append("content not required")
    if not callable(tool.get("func")):
        fail.append("func not callable")
    assert not (fail), "Test failed"


# ══════════════════════════════════════════════════════════════════════
# sandbox_list_dir tests
# ══════════════════════════════════════════════════════════════════════

def test_list_dir_sandbox_not_available():
    """g_data has no sandbox_manager → tool_error."""
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = None
        raw = asyncio.run(sandbox_list_dir("/workspace"))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("Sandbox is not available" not in r.get("error", "")), "Test failed"


def test_list_dir_success():
    """ls -la output is parsed into structured entries."""
    sandbox = _make_sandbox()
    sandbox.run.return_value = {
        "status": "done",
        "exit_code": 0,
        "output": (
            "total 16\n"
            "drwxr-xr-x 2 root root 4096 2026-05-08 12:00 .\n"
            "drwxr-xr-x 1 root root 4096 2026-05-08 12:00 ..\n"
            "-rw-r--r-- 1 root root  123 2026-05-08 12:00 main.py\n"
            "drwxr-xr-x 2 root root 4096 2026-05-08 11:30 src\n"
            "lrwxrwxrwx 1 root root   10 2026-05-08 10:00 link.txt -> /tmp/target\n"
        ),
    }
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_list_dir("/workspace"))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    entries = r.get("data", [])
    assert not (len(entries) != 3), "Test failed"
    assert not (r.get("count") != 3), "Test failed"
    # Check file entry
    main_py = next((e for e in entries if e["name"] == "main.py"), None)
    assert not (not main_py), "Test failed"
    assert not (main_py["type"] != "file"), "Test failed"
    assert not (main_py["size"] != 123), "Test failed"
    # Check directory entry
    src = next((e for e in entries if e["name"] == "src"), None)
    assert not (not src or src["type"] != "directory"), "Test failed"
    # Check symlink entry
    link = next((e for e in entries if e["name"] == "link.txt"), None)
    assert not (not link or link["type"] != "symlink"), "Test failed"
    assert not (link.get("target") != "/tmp/target"), "Test failed"


def test_list_dir_default_path():
    """Default path is /workspace when not specified."""
    sandbox = _make_sandbox()
    sandbox.run.return_value = {
        "status": "done",
        "exit_code": 0,
        "output": "total 0\n",
    }
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_list_dir())
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    call_arg = sandbox.run.call_args[0][0]
    assert not ("/workspace" not in call_arg), "Test failed"


def test_list_dir_empty():
    """Empty directory (only . and ..) → empty entries list."""
    sandbox = _make_sandbox()
    sandbox.run.return_value = {
        "status": "done",
        "exit_code": 0,
        "output": (
            "total 8\n"
            "drwxr-xr-x 2 root root 4096 2026-05-08 12:00 .\n"
            "drwxr-xr-x 1 root root 4096 2026-05-08 12:00 ..\n"
        ),
    }
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_list_dir("/workspace"))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r.get("data") != []), "Test failed"


def test_list_dir_exception():
    """Sandbox.run raises → tool_error."""
    sandbox = _make_sandbox()
    sandbox.run.side_effect = RuntimeError("connection timeout")
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(sandbox_list_dir())
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not (r.get("error") not in ("SSH connection lost", "disk full", "connection timeout")), "Test failed"


def test_list_dir_get_tool():
    """get_tool() returns valid schema for sandbox_list_dir."""
    tool = list_dir_tool()[0]
    fail = []
    if tool.get("type") != "function":
        fail.append(f"type={tool.get('type')}")
    fn = tool.get("function", {})
    if fn.get("name") != "sandbox_list_dir":
        fail.append(f"name={fn.get('name')}")
    if tool.get("safe") is not True:
        fail.append(f"safe={tool.get('safe')}")
    if "sandbox" not in tool.get("categories", []):
        fail.append(f"categories={tool.get('categories')}")
    props = fn.get("parameters", {}).get("properties", {})
    if "path" not in props:
        fail.append("missing path property")
    required = fn.get("parameters", {}).get("required", [])
    if required != []:
        fail.append(f"required should be empty (path is optional), got {required}")
    if not callable(tool.get("func")):
        fail.append("func not callable")
    assert not (fail), "Test failed"


# ══════════════════════════════════════════════════════════════════════
# _parse_ls_output unit tests
# ══════════════════════════════════════════════════════════════════════

def test_parse_ls_output_skips_total_line():
    """total line is skipped."""
    entries = _parse_ls_output("total 123\n-rw-r--r-- 1 root root 0 2026-05-08 12:00 test.txt")
    assert not (len(entries) != 1), "Test failed"


def test_parse_ls_output_skips_empty_lines():
    """Empty lines are skipped."""
    entries = _parse_ls_output("\n\n-rw-r--r-- 1 root root 0 2026-05-08 12:00 test.txt\n\n")
    assert not (len(entries) != 1), "Test failed"


# ══════════════════════════════════════════════════════════════════════
