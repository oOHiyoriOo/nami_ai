"""
Tests for SandboxManager tool wrappers: get_job_output, kill_job, list_jobs, reset_sandbox.

Covers:
- Sandbox-unavailable error path (all four tools)
- Successful delegation to SandboxManager
- Exception wrapping
- get_tool() schema validation
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from OllamaTools.get_job_output import get_job_output, get_tool as get_job_output_tool
from OllamaTools.kill_job import kill_job, get_tool as kill_job_tool
from OllamaTools.list_jobs import list_jobs, get_tool as list_jobs_tool
from OllamaTools.reset_sandbox import reset_sandbox, get_tool as reset_sandbox_tool


def _parse(raw: str) -> dict:
    return json.loads(raw)


def _make_sandbox():
    """Create a MagicMock sandbox_manager with common methods."""
    sandbox = MagicMock()
    sandbox.get_output = MagicMock()
    sandbox.kill_job = MagicMock()
    sandbox.list_jobs = MagicMock()
    sandbox.reset = AsyncMock()
    return sandbox


# ══════════════════════════════════════════════════════════════════════
# get_job_output tests
# ══════════════════════════════════════════════════════════════════════

def test_get_job_output_sandbox_not_available():
    """g_data has no sandbox_manager → tool_error."""
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = None
        raw = asyncio.run(get_job_output("abc123"))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("Sandbox is not available" not in r.get("error", "")), "Test failed"


def test_get_job_output_running():
    """Sandbox returns running status → tool_success with job data."""
    sandbox = _make_sandbox()
    sandbox.get_output.return_value = {
        "status": "running",
        "job_id": "abc123",
        "output": "processing...\n",
        "exit_code": None,
    }
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(get_job_output("abc123"))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r["data"].get("status") != "running"), "Test failed"
    sandbox.get_output.assert_called_once_with("abc123")


def test_get_job_output_done():
    """Sandbox returns done status with exit_code."""
    sandbox = _make_sandbox()
    sandbox.get_output.return_value = {
        "status": "done",
        "job_id": "def456",
        "output": "file1\nfile2\n",
        "exit_code": 0,
    }
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(get_job_output("def456"))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r["data"].get("exit_code") != 0), "Test failed"
    sandbox.get_output.assert_called_once_with("def456")


def test_get_job_output_not_found():
    """Sandbox returns not_found → tool_success (not error — job just not found)."""
    sandbox = _make_sandbox()
    sandbox.get_output.return_value = {"status": "not_found", "job_id": "xyz"}
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(get_job_output("xyz"))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r["data"].get("status") != "not_found"), "Test failed"


def test_get_job_output_exception():
    """Sandbox.get_output raises → tool_error with exception message."""
    sandbox = _make_sandbox()
    sandbox.get_output.side_effect = RuntimeError("connection lost")
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(get_job_output("abc"))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("connection lost" not in r.get("error", "")), "Test failed"


def test_get_job_output_get_tool():
    """get_tool() returns valid schema for get_job_output."""
    tool = get_job_output_tool()[0]
    fail = []
    if tool.get("type") != "function":
        fail.append(f"type={tool.get('type')}")
    fn = tool.get("function", {})
    if fn.get("name") != "get_job_output":
        fail.append(f"name={fn.get('name')}")
    if tool.get("safe") is not True:
        fail.append(f"safe={tool.get('safe')}")
    if "sandbox" not in tool.get("categories", []):
        fail.append(f"categories={tool.get('categories')}")
    if "job_id" not in fn.get("parameters", {}).get("properties", {}):
        fail.append("missing job_id property")
    if "job_id" not in fn.get("parameters", {}).get("required", []):
        fail.append("job_id not required")
    if not callable(tool.get("func")):
        fail.append("func not callable")
    assert not (fail), "Test failed"


# ══════════════════════════════════════════════════════════════════════
# kill_job tests
# ══════════════════════════════════════════════════════════════════════

def test_kill_job_sandbox_not_available():
    """g_data has no sandbox_manager → tool_error."""
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = None
        raw = asyncio.run(kill_job("abc123"))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("Sandbox is not available" not in r.get("error", "")), "Test failed"


def test_kill_job_not_found():
    """Sandbox returns not_found for unknown job_id → tool_success."""
    sandbox = _make_sandbox()
    sandbox.kill_job.return_value = {"status": "not_found", "job_id": "xyz"}
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(kill_job("xyz"))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r["data"].get("status") != "not_found"), "Test failed"
    sandbox.kill_job.assert_called_once_with("xyz")


def test_kill_job_killed():
    """Sandbox successfully kills a running job."""
    sandbox = _make_sandbox()
    sandbox.kill_job.return_value = {"status": "killed", "job_id": "abc"}
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(kill_job("abc"))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r["data"].get("status") != "killed"), "Test failed"
    sandbox.kill_job.assert_called_once_with("abc")


def test_kill_job_already_done():
    """Sandbox says job already finished → tool_success."""
    sandbox = _make_sandbox()
    sandbox.kill_job.return_value = {"status": "already_done", "job_id": "def"}
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(kill_job("def"))
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r["data"].get("status") != "already_done"), "Test failed"


def test_kill_job_exception():
    """Sandbox.kill_job raises → tool_error."""
    sandbox = _make_sandbox()
    sandbox.kill_job.side_effect = RuntimeError("process group gone")
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(kill_job("abc"))
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("process group gone" not in r.get("error", "")), "Test failed"


def test_kill_job_get_tool():
    """get_tool() returns valid schema for kill_job."""
    tool = kill_job_tool()[0]
    fail = []
    if tool.get("type") != "function":
        fail.append(f"type={tool.get('type')}")
    fn = tool.get("function", {})
    if fn.get("name") != "kill_job":
        fail.append(f"name={fn.get('name')}")
    if tool.get("safe") is not False:
        fail.append(f"safe={tool.get('safe')}")
    if "sandbox_dangerous" not in tool.get("categories", []):
        fail.append(f"categories={tool.get('categories')}")
    if "job_id" not in fn.get("parameters", {}).get("properties", {}):
        fail.append("missing job_id property")
    if "job_id" not in fn.get("parameters", {}).get("required", []):
        fail.append("job_id not required")
    if not callable(tool.get("func")):
        fail.append("func not callable")
    assert not (fail), "Test failed"


# ══════════════════════════════════════════════════════════════════════
# list_jobs tests
# ══════════════════════════════════════════════════════════════════════

def test_list_jobs_sandbox_not_available():
    """g_data has no sandbox_manager → tool_error."""
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = None
        raw = asyncio.run(list_jobs())
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("Sandbox is not available" not in r.get("error", "")), "Test failed"


def test_list_jobs_empty():
    """Sandbox returns empty list → tool_success with 'No jobs tracked yet.' string."""
    sandbox = _make_sandbox()
    sandbox.list_jobs.return_value = []
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(list_jobs())
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r.get("data") != "No jobs tracked yet."), "Test failed"


def test_list_jobs_with_jobs():
    """Sandbox returns job list → tool_success wrapping the list."""
    jobs_list = [
        {"job_id": "a1", "command": "sleep 60", "status": "running", "elapsed_seconds": 5.2},
        {"job_id": "b2", "command": "echo done", "status": "done", "exit_code": 0, "elapsed_seconds": 0.3},
    ]
    sandbox = _make_sandbox()
    sandbox.list_jobs.return_value = jobs_list
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(list_jobs())
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r.get("data") != jobs_list), "Test failed"
    sandbox.list_jobs.assert_called_once()


def test_list_jobs_exception():
    """Sandbox.list_jobs raises → tool_error."""
    sandbox = _make_sandbox()
    sandbox.list_jobs.side_effect = RuntimeError("registry corrupted")
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(list_jobs())
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("registry corrupted" not in r.get("error", "")), "Test failed"


def test_list_jobs_get_tool():
    """get_tool() returns valid schema for list_jobs."""
    tool = list_jobs_tool()[0]
    fail = []
    if tool.get("type") != "function":
        fail.append(f"type={tool.get('type')}")
    fn = tool.get("function", {})
    if fn.get("name") != "list_jobs":
        fail.append(f"name={fn.get('name')}")
    if tool.get("safe") is not True:
        fail.append(f"safe={tool.get('safe')}")
    if "sandbox" not in tool.get("categories", []):
        fail.append(f"categories={tool.get('categories')}")
    if fn.get("parameters", {}).get("properties") != {}:
        fail.append("parameters.properties should be empty")
    if fn.get("parameters", {}).get("required") != []:
        fail.append("parameters.required should be empty")
    if not callable(tool.get("func")):
        fail.append("func not callable")
    assert not (fail), "Test failed"


# ══════════════════════════════════════════════════════════════════════
# reset_sandbox tests
# ══════════════════════════════════════════════════════════════════════

def test_reset_sandbox_not_available():
    """g_data has no sandbox_manager → tool_error."""
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = None
        raw = asyncio.run(reset_sandbox())
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("Sandbox is not available" not in r.get("error", "")), "Test failed"


def test_reset_sandbox_success():
    """Sandbox.reset returns ok → tool_success."""
    sandbox = _make_sandbox()
    sandbox.reset.return_value = {"status": "ok", "message": "Sandbox workspace wiped. All jobs cleared."}
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(reset_sandbox())
    r = _parse(raw)
    assert not (not r.get("success")), "Test failed"
    assert not (r["data"].get("status") != "ok"), "Test failed"
    sandbox.reset.assert_called_once()


def test_reset_sandbox_exception():
    """Sandbox.reset raises → tool_error."""
    sandbox = _make_sandbox()
    sandbox.reset.side_effect = RuntimeError("SSH connection refused")
    with patch("OllamaTools.g_data") as g:
        g.get.return_value = sandbox
        raw = asyncio.run(reset_sandbox())
    r = _parse(raw)
    assert not (r.get("success") is not False), "Test failed"
    assert not ("SSH connection refused" not in r.get("error", "")), "Test failed"


def test_reset_sandbox_get_tool():
    """get_tool() returns valid schema for reset_sandbox."""
    tool = reset_sandbox_tool()[0]
    fail = []
    if tool.get("type") != "function":
        fail.append(f"type={tool.get('type')}")
    fn = tool.get("function", {})
    if fn.get("name") != "reset_sandbox":
        fail.append(f"name={fn.get('name')}")
    if tool.get("safe") is not False:
        fail.append(f"safe={tool.get('safe')}")
    if "sandbox_dangerous" not in tool.get("categories", []):
        fail.append(f"categories={tool.get('categories')}")
    if fn.get("parameters", {}).get("properties") != {}:
        fail.append("parameters.properties should be empty")
    if fn.get("parameters", {}).get("required") != []:
        fail.append("parameters.required should be empty")
    if not callable(tool.get("func")):
        fail.append("func not callable")
    assert not (fail), "Test failed"
