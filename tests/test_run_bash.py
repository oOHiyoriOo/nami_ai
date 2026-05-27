"""
Tests for OllamaTools/run_bash.py — sandbox command execution tool.

Covers:
- get_tool() structure validation
- Successful command execution
- Special character handling (shlex quoting via sandbox)
- Sandbox unavailable error
- SSH failure graceful handling
- Output truncation for large results
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from OllamaTools.run_bash import run_bash, get_tool


# ── get_tool() tests ─────────────────────────────────────────────────


def test_get_tool_structure():
    """get_tool() returns properly structured tool definition."""
    tool = get_tool()
    assert tool.get("type") == "function", f"[FAIL] Expected type='function'"
    assert tool.get("function"), f"[FAIL] Missing 'function' key"
    fn = tool["function"]
    assert fn.get("name") == "run_bash", f"[FAIL] Expected name='run_bash', got {fn.get('name')!r}"
    assert "command" in fn.get("parameters", {}).get("properties", {}), f"[FAIL] Missing 'command' parameter"
    assert "command" in fn.get("parameters", {}).get("required", []), f"[FAIL] 'command' should be required"


def test_get_tool_safe_is_false():
    """get_tool() marks tool as safe=False (dangerous sandbox tool)."""
    tool = get_tool()
    assert tool.get("safe") is False, f"[FAIL] Expected safe=False, got {tool.get('safe')!r}"


def test_get_tool_func_is_callable():
    """get_tool() 'func' key points to callable run_bash."""
    tool = get_tool()
    func = tool.get("func")
    assert callable(func), f"[FAIL] 'func' is not callable: {type(func)}"


def test_get_tool_categories():
    """get_tool() includes sandbox_dangerous category."""
    tool = get_tool()
    cats = tool.get("categories", [])
    assert "sandbox_dangerous" in cats, f"[FAIL] Expected 'sandbox_dangerous' in categories, got {cats}"


# ── run_bash() tests ─────────────────────────────────────────────────


def _make_sandbox_mock(return_value=None, side_effect=None):
    """Create a mock sandbox manager with async run()."""
    mock = MagicMock()
    mock.run = AsyncMock()
    if side_effect:
        mock.run.side_effect = side_effect
    elif return_value is not None:
        mock.run.return_value = return_value
    return mock


def _parse_result(raw: str) -> dict:
    return json.loads(raw)


def test_successful_command():
    """Mock sandbox returns success → tool_success with output."""
    sandbox = _make_sandbox_mock(return_value={
        "status": "done",
        "exit_code": 0,
        "output": "file1.txt\nfile2.txt\n",
    })

    with patch("OllamaTools.run_bash.g_data") as mock_g_data:
        mock_g_data.get.return_value = sandbox
        raw = asyncio.run(run_bash("ls /workspace"))

    result = _parse_result(raw)
    assert result.get("success"), f"[FAIL] Expected success=True, got {result}"
    assert result.get("data", {}).get("output") == "file1.txt\nfile2.txt\n", f"[FAIL] Unexpected output data: {result.get('data')!r}"
    assert result.get("command") == "ls /workspace", f"[FAIL] Expected command='ls /workspace', got {result.get('command')!r}"
    sandbox.run.assert_called_once_with("ls /workspace")


def test_command_with_special_characters():
    """Command with special chars ($, ;, |, `) is passed through to sandbox."""
    cmd = "echo 'hello world' && ls -la | grep foo; echo $HOME"
    sandbox = _make_sandbox_mock(return_value={
        "status": "done",
        "exit_code": 0,
        "output": "hello world\n-rw-r--r-- 1 root root 0 file\n/root\n",
    })

    with patch("OllamaTools.run_bash.g_data") as mock_g_data:
        mock_g_data.get.return_value = sandbox
        raw = asyncio.run(run_bash(cmd))

    result = _parse_result(raw)
    assert result.get("success"), f"[FAIL] Special chars should not cause failure: {result}"
    # The command MUST be the full original string — it gets shlex.quoted inside the sandbox
    sandbox.run.assert_called_once_with(cmd)


def test_command_with_shell_metacharacters():
    """Command with shell metacharacters (backticks, redirects, subshells)."""
    cmd = 'cat /etc/passwd | cut -d: -f1 > /tmp/users.txt && wc -l < /tmp/users.txt'
    sandbox = _make_sandbox_mock(return_value={
        "status": "done",
        "exit_code": 0,
        "output": "5\n",
    })

    with patch("OllamaTools.run_bash.g_data") as mock_g_data:
        mock_g_data.get.return_value = sandbox
        raw = asyncio.run(run_bash(cmd))

    result = _parse_result(raw)
    assert result.get("success"), f"[FAIL] Shell metacharacters should not cause failure: {result}"
    sandbox.run.assert_called_once_with(cmd)


def test_sandbox_unavailable():
    """g_data has no sandbox_manager → tool_error returned."""
    with patch("OllamaTools.run_bash.g_data") as mock_g_data:
        mock_g_data.get.return_value = None
        raw = asyncio.run(run_bash("echo hello"))

    result = _parse_result(raw)
    assert result.get("success") is False, f"[FAIL] Expected success=False, got {result}"
    assert "Sandbox is not available" in result.get("error", ""), f"[FAIL] Expected 'Sandbox is not available', got {result.get('error')!r}"


def test_ssh_failure():
    """sandbox.run() raises exception → graceful error via tool_error."""
    sandbox = _make_sandbox_mock(side_effect=ConnectionError("SSH connection refused"))

    with patch("OllamaTools.run_bash.g_data") as mock_g_data:
        mock_g_data.get.return_value = sandbox
        raw = asyncio.run(run_bash("echo hello"))

    result = _parse_result(raw)
    assert result.get("success") is False, f"[FAIL] Expected success=False on SSH failure, got {result}"
    assert "SSH connection refused" in result.get("error", ""), f"[FAIL] Expected ConnectionError in message, got {result.get('error')!r}"
    assert result.get("command") == "echo hello", f"[FAIL] Expected command in error context"


def test_sandbox_run_raises_timeout():
    """sandbox.run() raises TimeoutError → graceful error."""
    sandbox = _make_sandbox_mock(side_effect=TimeoutError("Command timed out"))

    with patch("OllamaTools.run_bash.g_data") as mock_g_data:
        mock_g_data.get.return_value = sandbox
        raw = asyncio.run(run_bash("sleep 999"))

    result = _parse_result(raw)
    assert result.get("success") is False, f"[FAIL] Expected success=False on timeout, got {result}"
    assert "Command timed out" in result.get("error", ""), f"[FAIL] Expected TimeoutError in message, got {result.get('error')!r}"


def test_output_truncation_large_result():
    """Sandbox returns large output → run_bash passes it through correctly."""
    large_output = "x" * 50000
    sandbox = _make_sandbox_mock(return_value={
        "status": "done",
        "exit_code": 0,
        "output": large_output,
    })

    with patch("OllamaTools.run_bash.g_data") as mock_g_data:
        mock_g_data.get.return_value = sandbox
        raw = asyncio.run(run_bash("cat /var/log/syslog"))

    result = _parse_result(raw)
    assert result.get("success"), f"[FAIL] Large output should be successful: {result}"
    data_output = result.get("data", {}).get("output", "")
    assert data_output == large_output, f"[FAIL] Expected full large output in data, got {len(data_output)} chars"
    sandbox.run.assert_called_once_with("cat /var/log/syslog")


def test_command_with_quotes_and_escapes():
    """Command with single quotes, double quotes, and backslash escapes."""
    cmd = """echo "it's a test" && echo 'she said "hello"' && echo path\\ with\\ spaces"""
    sandbox = _make_sandbox_mock(return_value={
        "status": "done",
        "exit_code": 0,
        "output": "it's a test\nshe said \"hello\"\npath with spaces\n",
    })

    with patch("OllamaTools.run_bash.g_data") as mock_g_data:
        mock_g_data.get.return_value = sandbox
        raw = asyncio.run(run_bash(cmd))

    result = _parse_result(raw)
    assert result.get("success"), f"[FAIL] Quoted/escaped command should succeed: {result}"
    sandbox.run.assert_called_once_with(cmd)


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))