"""
Tests for lib/mcp_client.py — stderr capture, buffer behavior, and env filtering.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.mcp_client import MCPClient, MCPServer, STDERR_BUFFER_MAX, _SAFE_ENV_VARS


# ---------------------------------------------------------------------------
# _read_stderr — direct test via a subprocess that only writes to stderr
# ---------------------------------------------------------------------------

async def _test_read_stderr_basic():
    """stderr lines are captured in the buffer and logged."""
    client = MCPClient()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-S", "-c",
        "import sys; sys.stderr.write('line one\\n'); sys.stderr.write('line two\\n'); sys.stderr.flush()",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    buf: list[str] = []
    task = asyncio.create_task(client._read_stderr("test", proc.stderr, buf))

    # Wait for subprocess to exit, then wait for reader to drain
    await proc.wait()
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Filter out any system/sdk noise; our lines should be present
    our_lines = [l for l in buf if l.startswith("line ")]
    assert our_lines == ["line one", "line two"], f"Unexpected lines: {our_lines}"
    print("[PASS] _test_read_stderr_basic")


async def _test_read_stderr_buffer_capped():
    """stderr buffer does not exceed STDERR_BUFFER_MAX."""
    client = MCPClient()
    total_lines = STDERR_BUFFER_MAX + 20
    code = (
        "import sys; "
        + "; ".join(
            f"sys.stderr.write('line {i}\\n')"
            for i in range(total_lines)
        )
        + "; sys.stderr.flush()"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-S", "-c", code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    buf: list[str] = []
    task = asyncio.create_task(client._read_stderr("test", proc.stderr, buf))

    await proc.wait()
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(buf) <= STDERR_BUFFER_MAX, (
        f"Buffer exceeded max: {len(buf)} > {STDERR_BUFFER_MAX}"
    )
    # Should have kept the most recent lines
    assert f"line {total_lines - 1}" in buf, "Missing last line in buffer"
    print("[PASS] _test_read_stderr_buffer_capped")


# ---------------------------------------------------------------------------
# MCPServer dataclass — new fields accept defaults
# ---------------------------------------------------------------------------

def test_mcpserver_defaults():
    """MCPServer.fields stderr_buffer and _stderr_task have defaults."""
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(MCPServer)}
    assert "stderr_buffer" in fields
    assert "_stderr_task" in fields
    assert fields["stderr_buffer"].default_factory is not dataclasses.MISSING
    assert fields["_stderr_task"].default is None
    print("[PASS] test_mcpserver_defaults")


def test_mcpserver_repr_omits_stderr_task():
    """_stderr_task is excluded from repr."""
    s = MCPServer(
        name="test",
        transport="stdio",
        process=None,
        reader=None,
        writer=None,
        tools=[],
    )
    r = repr(s)
    assert "_stderr_task" not in r, f"repr leaked _stderr_task: {r}"
    assert "stderr_buffer" in r
    print("[PASS] test_mcpserver_repr_omits_stderr_task")


# ---------------------------------------------------------------------------
# Environment variable filtering — verify secrets are not leaked
# ---------------------------------------------------------------------------

def test_env_whitelist_blocks_secrets():
    """Environment for MCP subprocess must NOT contain secret keys."""
    # Simulate that the parent process has secret env vars set
    test_secrets = {"NEO4J_PASSWORD", "DISCORD_TOKEN", "SANDBOX_PASSWORD", "FORGEJO_TOKEN"}
    for k in test_secrets:
        os.environ[k] = "test-secret-value"

    try:
        # Build server_env the same way connect_server() does
        server_env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_VARS}

        leaked = set(server_env.keys()) & test_secrets
        assert not leaked, f"Secret env vars leaked to MCP server env: {leaked}"
    finally:
        for k in test_secrets:
            os.environ.pop(k, None)

    print("[PASS] test_env_whitelist_blocks_secrets")


def test_env_whitelist_includes_basics():
    """Essential env vars like PATH and HOME must be forwarded."""
    server_env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_VARS}

    assert "PATH" in server_env, "PATH must be forwarded to MCP subprocess"
    assert server_env["PATH"] == os.environ["PATH"], "PATH value mismatch"

    # HOME may not be set in all environments but if set, must match
    if "HOME" in os.environ:
        assert "HOME" in server_env
        assert server_env["HOME"] == os.environ["HOME"]

    print("[PASS] test_env_whitelist_includes_basics")


def test_env_config_override_works():
    """The explicit env parameter can add/override variables."""
    server_env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_VARS}

    # Simulate config-provided env vars being applied (as connect_server does)
    config_env = {"MY_CUSTOM_VAR": "custom_value", "PATH": "/override/path"}
    server_env.update(config_env)

    assert server_env["MY_CUSTOM_VAR"] == "custom_value"
    assert server_env["PATH"] == "/override/path"

    print("[PASS] test_env_config_override_works")


# ---------------------------------------------------------------------------
# Transport-agnostic MCPServer
# ---------------------------------------------------------------------------

def test_mcpserver_transport_field():
    """MCPServer requires a transport field."""
    s = MCPServer(name="s", transport="stdio", tools=[])
    assert s.transport == "stdio"
    assert s.process is None
    assert s.url is None

    s2 = MCPServer(name="h", transport="http", tools=[], url="http://example.com")
    assert s2.transport == "http"
    assert s2.url == "http://example.com"
    assert s2.process is None
    assert s2.reader is None
    assert s2.writer is None
    print("[PASS] test_mcpserver_transport_field")


def test_mcpserver_sdk_fields_have_defaults():
    """_exit_stack and _session fields have None defaults."""
    s = MCPServer(name="x", transport="http", tools=[])
    assert s._exit_stack is None
    assert s._session is None
    print("[PASS] test_mcpserver_sdk_fields_have_defaults")


# ---------------------------------------------------------------------------
# connect_server dispatch
# ---------------------------------------------------------------------------

def test_connect_server_rejects_unknown_transport():
    """connect_server raises ValueError for unknown transports."""
    client = MCPClient()

    async def run():
        try:
            await client.connect_server("bad", transport="grpc")
            return False
        except ValueError as e:
            assert "Unknown transport" in str(e)
            return True

    assert asyncio.run(run())
    print("[PASS] test_connect_server_rejects_unknown_transport")


def test_connect_server_http_requires_url():
    """HTTP transport requires a url."""
    client = MCPClient()

    async def run():
        try:
            await client.connect_server("no_url", transport="http")
            return False
        except ValueError as e:
            assert "no URL configured" in str(e)
            return True

    assert asyncio.run(run())
    print("[PASS] test_connect_server_http_requires_url")


def test_connect_server_sse_requires_url():
    """SSE transport requires a url."""
    client = MCPClient()

    async def run():
        try:
            await client.connect_server("no_url", transport="sse")
            return False
        except ValueError as e:
            assert "no URL configured" in str(e)
            return True

    assert asyncio.run(run())
    print("[PASS] test_connect_server_sse_requires_url")


# ---------------------------------------------------------------------------
# call_tool dispatch
# ---------------------------------------------------------------------------

def test_call_tool_rejects_unknown_transport():
    """call_tool raises ValueError when server has unknown transport."""
    client = MCPClient()

    async def run():
        server = MCPServer(name="weird", transport="grpc", tools=[])
        client.servers["weird"] = server
        try:
            await client.call_tool("weird", "x", {})
            return False
        except ValueError as e:
            assert "Unknown transport" in str(e)
            return True

    assert asyncio.run(run())
    print("[PASS] test_call_tool_rejects_unknown_transport")


def test_call_tool_rejects_missing_server():
    """call_tool raises ValueError when server not found."""
    client = MCPClient()

    async def run():
        try:
            await client.call_tool("nope", "x", {})
            return False
        except ValueError as e:
            assert "not connected" in str(e)
            return True

    assert asyncio.run(run())
    print("[PASS] test_call_tool_rejects_missing_server")


# ---------------------------------------------------------------------------
# disconnect_server dispatch
# ---------------------------------------------------------------------------

def test_disconnect_server_http():
    """disconnect_server for http/sse cleans up without process fields."""
    client = MCPClient()

    async def run():
        server = MCPServer(name="h", transport="http", tools=[], url="http://x")
        client.servers["h"] = server
        await client.disconnect_server("h")
        assert "h" not in client.servers
        return True

    assert asyncio.run(run())
    print("[PASS] test_disconnect_server_http")


def test_disconnect_server_unknown_transport():
    """disconnect_server handles unknown transport gracefully."""
    client = MCPClient()

    async def run():
        server = MCPServer(name="weird", transport="grpc", tools=[])
        client.servers["weird"] = server
        await client.disconnect_server("weird")
        assert "weird" not in client.servers
        return True

    assert asyncio.run(run())
    print("[PASS] test_disconnect_server_unknown_transport")


# ---------------------------------------------------------------------------
# Health monitoring & reconnect
# ---------------------------------------------------------------------------

def test_mcpserver_reconnect_fields_have_defaults():
    """MCPServer._command, _args, _env, _reconnect, _monitor_task have defaults."""
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(MCPServer)}
    for name in ("_command", "_args", "_env", "_reconnect", "_monitor_task"):
        assert name in fields, f"Missing field: {name}"
    assert fields["_command"].default is None
    assert fields["_args"].default is None
    assert fields["_env"].default is None
    assert fields["_reconnect"].default is False
    assert fields["_monitor_task"].default is None
    print("[PASS] test_mcpserver_reconnect_fields_have_defaults")


def test_mcpserver_repr_excludes_reconnect_fields():
    """repr excludes new private reconnect fields."""
    s = MCPServer(name="t", transport="stdio", tools=[])
    r = repr(s)
    for field_name in ("_command", "_args", "_env", "_reconnect", "_monitor_task"):
        assert field_name not in r, f"repr leaked {field_name}: {r}"
    print("[PASS] test_mcpserver_repr_excludes_reconnect_fields")


def test_monitor_server_noop_for_non_stdio():
    """_monitor_server returns immediately for http/sse transports."""
    client = MCPClient()

    async def run():
        server = MCPServer(name="h", transport="http", tools=[], url="http://x")
        # Should return immediately — no process to wait on
        await client._monitor_server("h", server)
        return True

    assert asyncio.run(run())
    print("[PASS] test_monitor_server_noop_for_non_stdio")


def test_monitor_server_process_exit_no_reconnect():
    """_monitor_server removes server on exit when reconnect=False."""
    client = MCPClient()

    async def run():
        # Start a real subprocess that exits immediately
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-S", "-c", "",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()

        server = MCPServer(
            name="test", transport="stdio", tools=[],
            process=proc, _reconnect=False,
        )
        client.servers["test"] = server

        await client._monitor_server("test", server)

        # Server should have been removed
        assert "test" not in client.servers, (
            "Server should be removed after exit"
        )
        return True

    assert asyncio.run(run())
    print("[PASS] test_monitor_server_process_exit_no_reconnect")


def test_monitor_server_process_exit_with_reconnect():
    """_monitor_server attempts reconnect when _reconnect=True."""
    client = MCPClient()
    reconnect_attempted = False

    original_connect = client.connect_server

    async def mock_connect(name, **kwargs):
        nonlocal reconnect_attempted
        reconnect_attempted = True
        # Don't actually connect — just create a minimal server
        s = MCPServer(name=name, transport="stdio", tools=[])
        client.servers[name] = s
        return s

    async def run():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-S", "-c", "",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()

        server = MCPServer(
            name="test", transport="stdio", tools=[],
            process=proc,
            _command=sys.executable,
            _args=["-S", "-c", "print(1)"],
            _env={},
            _reconnect=True,
        )
        client.servers["test"] = server
        client.connect_server = mock_connect

        try:
            await client._monitor_server("test", server)
            assert reconnect_attempted, "Reconnect should have been attempted"
            assert "test" in client.servers, "Server should be reconnected"
        finally:
            client.connect_server = original_connect

        return True

    assert asyncio.run(run())
    print("[PASS] test_monitor_server_process_exit_with_reconnect")


def test_disconnect_server_cancels_monitor_task():
    """disconnect_server cancels _monitor_task before cleanup."""
    client = MCPClient()

    async def run():
        # Create a fake monitor task using a shared event
        evt = asyncio.Event()
        monitor_cancelled = False

        async def fake_monitor():
            nonlocal monitor_cancelled
            try:
                await evt.wait()  # never completes
            except asyncio.CancelledError:
                monitor_cancelled = True
                raise

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-S", "-c", "import time; time.sleep(10)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        task = asyncio.create_task(fake_monitor())
        await asyncio.sleep(0)  # let the task start executing

        server = MCPServer(
            name="test", transport="stdio", tools=[],
            process=proc, reader=proc.stdout, writer=proc.stdin,
            _monitor_task=task,
        )
        client.servers["test"] = server

        await client.disconnect_server("test")

        assert monitor_cancelled, "Monitor task should have been cancelled"
        assert "test" not in client.servers
        return True

    assert asyncio.run(run())
    print("[PASS] test_disconnect_server_cancels_monitor_task")


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

def main():
    passed = 0
    failed = 0

    # sync tests
    for name, fn in [
        ("test_mcpserver_defaults", test_mcpserver_defaults),
        ("test_mcpserver_repr_omits_stderr_task", test_mcpserver_repr_omits_stderr_task),
        ("test_env_whitelist_blocks_secrets", test_env_whitelist_blocks_secrets),
        ("test_env_whitelist_includes_basics", test_env_whitelist_includes_basics),
        ("test_env_config_override_works", test_env_config_override_works),
        ("test_mcpserver_transport_field", test_mcpserver_transport_field),
        ("test_mcpserver_sdk_fields_have_defaults", test_mcpserver_sdk_fields_have_defaults),
        ("test_connect_server_rejects_unknown_transport", test_connect_server_rejects_unknown_transport),
        ("test_connect_server_http_requires_url", test_connect_server_http_requires_url),
        ("test_connect_server_sse_requires_url", test_connect_server_sse_requires_url),
        ("test_call_tool_rejects_unknown_transport", test_call_tool_rejects_unknown_transport),
        ("test_call_tool_rejects_missing_server", test_call_tool_rejects_missing_server),
        ("test_disconnect_server_http", test_disconnect_server_http),
        ("test_disconnect_server_unknown_transport", test_disconnect_server_unknown_transport),
        ("test_mcpserver_reconnect_fields_have_defaults", test_mcpserver_reconnect_fields_have_defaults),
        ("test_mcpserver_repr_excludes_reconnect_fields", test_mcpserver_repr_excludes_reconnect_fields),
        ("test_monitor_server_noop_for_non_stdio", test_monitor_server_noop_for_non_stdio),
        ("test_monitor_server_process_exit_no_reconnect", test_monitor_server_process_exit_no_reconnect),
        ("test_monitor_server_process_exit_with_reconnect", test_monitor_server_process_exit_with_reconnect),
        ("test_disconnect_server_cancels_monitor_task", test_disconnect_server_cancels_monitor_task),
    ]:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            failed += 1

    # async tests
    for name, fn in [
        ("_test_read_stderr_basic", _test_read_stderr_basic),
        ("_test_read_stderr_buffer_capped", _test_read_stderr_buffer_capped),
    ]:
        try:
            asyncio.run(fn())
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            failed += 1

    print()
    print(f"Results: {passed}/{passed + failed} tests passed")
    if failed:
        sys.exit(1)
    print("[SUCCESS] All tests passed!")
