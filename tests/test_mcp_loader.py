"""
Tests for lib/utils/mcp_loader.py — load_mcp_tools()

Covers:
- Returns [] when config/registry is not available
- Returns [] when mcp_servers is empty/missing
- Skips disabled servers
- Skips stdio server with no command
- Resolves ${ENV_VAR} placeholders
- Passes unresolved ${VAR} when env var not set
- Skips http/sse server with no url
- Skips server with unknown transport
- Connects stdio server with correct args and resolved env
- Connects http/sse server with correct url
- Collects tools from multiple servers
- Catches connection exception without blocking others
- Reuses existing MCP client from g_data
"""

import asyncio
import io
import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.global_registry import g_data, GlobalRegistry
from lib.configuration_file import ConfigurationFile
from lib.mcp_client import MCPServer, MCPClient
from lib.utils.mcp_loader import load_mcp_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_registry():
    """Reset singleton's registry to a clean state."""
    g_data._registry.clear()
    return g_data


def _make_cfg(**mcp_servers_data):
    """Create a ConfigurationFile with given mcp_servers data."""
    _fresh_registry()
    data = {
        'default_provider': 'ollama',
        'default_model': 'test-model',
        'providers': {'ollama': {'url': 'http://localhost:11434'}},
    }
    if mcp_servers_data:
        data['mcp_servers'] = mcp_servers_data
    cfg = ConfigurationFile('test-config.yml', data)
    g_data._registry['cfg'] = cfg
    return cfg


def _make_mock_server(name, transport, tools):
    """Create an MCPServer with given tools."""
    return MCPServer(name=name, transport=transport, tools=tools)


def _make_mock_client(server_map=None):
    """Create a mock MCPClient that returns the given servers from connect_server."""
    client = MagicMock(spec=MCPClient)
    client.servers = {}
    client.connect_server = AsyncMock()

    if server_map:
        async def _connect(name, **kwargs):
            s = server_map.get(name)
            if s is None:
                raise Exception(f"No mock server for {name}")
            return s
        client.connect_server.side_effect = _connect

    return client


def _capture_logs(level=logging.WARNING):
    """Capture log messages for assertions."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(level)
    logger = logging.getLogger()
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(level)
    return buf, handler, old_level


def _release_logs(buf, handler, old_level):
    """Release log capture."""
    logger = logging.getLogger()
    logger.removeHandler(handler)
    logger.setLevel(old_level)


# ---------------------------------------------------------------------------
# Test: Returns [] when config is not available
# ---------------------------------------------------------------------------

def test_returns_empty_when_config_none():
    """Returns [] when g_data.get('cfg') is None."""
    _fresh_registry()  # no cfg in registry

    async def run():
        return await load_mcp_tools()

    result = asyncio.run(run())
    assert result == [], f"expected [], got {result}"


# ---------------------------------------------------------------------------
# Test: Returns [] when mcp_servers is empty/missing
# ---------------------------------------------------------------------------

def test_returns_empty_when_mcp_servers_missing():
    """Returns [] when config has no mcp_servers key."""
    _make_cfg()  # no mcp_servers key

    async def run():
        return await load_mcp_tools()

    result = asyncio.run(run())
    assert result == [], f"expected [], got {result}"


def test_returns_empty_when_mcp_servers_empty():
    """Returns [] when mcp_servers is an empty dict."""
    _make_cfg()
    g_data.get("cfg").data['mcp_servers'] = {}

    async def run():
        return await load_mcp_tools()

    result = asyncio.run(run())
    assert result == [], f"expected [], got {result}"


# ---------------------------------------------------------------------------
# Test: Skips disabled server
# ---------------------------------------------------------------------------

def test_skips_disabled_server():
    """Skips server with enabled: false."""
    _make_cfg(disabled_srv={"enabled": False, "transport": "stdio", "command": "echo"})

    client = _make_mock_client()
    g_data._registry['mcp_client'] = client

    async def run():
        return await load_mcp_tools()

    result = asyncio.run(run())
    if result != []:
        assert False, f"expected [], got {result}"
    assert not (client.connect_server.called)


# ---------------------------------------------------------------------------
# Test: Skips stdio server with no command
# ---------------------------------------------------------------------------

def test_skips_stdio_no_command():
    """Skips stdio server when command is not configured (logs error)."""
    _make_cfg(srv={"enabled": True, "transport": "stdio"})

    client = _make_mock_client()
    g_data._registry['mcp_client'] = client

    buf, handler, old_level = _capture_logs(logging.ERROR)

    try:
        async def run():
            return await load_mcp_tools()

        result = asyncio.run(run())
        log_output = buf.getvalue()

        if result != []:
            assert False, f"expected [], got {result}"
        if client.connect_server.called:
            assert False, f"connect_server should not be called"
        if "has no command configured" not in log_output:
            assert False, f"missing error log about command, got: {log_output}"
    finally:
        _release_logs(buf, handler, old_level)



# ---------------------------------------------------------------------------
# Test: Resolves ${VAR} environment variable placeholders
# ---------------------------------------------------------------------------

def test_resolves_env_var_placeholders():
    """Resolves ${VAR} to actual environment variable value."""
    os.environ['TEST_MCP_TOKEN'] = 'secret-token-123'

    try:
        _make_cfg(srv={
            "enabled": True,
            "transport": "stdio",
            "command": "my-tool",
            "args": ["--serve"],
            "env": {"API_KEY": "${TEST_MCP_TOKEN}", "MODE": "production"},
        })

        mock_server = _make_mock_server("srv", "stdio", [{"type": "function", "function": {"name": "t1"}}])
        client = _make_mock_client({"srv": mock_server})
        g_data._registry['mcp_client'] = client

        async def run():
            return await load_mcp_tools()

        result = asyncio.run(run())

        if len(result) != 1:
            assert False, f"expected 1 tool, got {len(result)}"
        # Verify connect_server was called with resolved env
        call_kwargs = client.connect_server.call_args.kwargs
        expected_env = {"API_KEY": "secret-token-123", "MODE": "production"}
        if call_kwargs.get("env") != expected_env:
            assert False, f"env mismatch: {call_kwargs.get('env')} != {expected_env}"
        if call_kwargs.get("command") != "my-tool":
            assert False, f"command mismatch: {call_kwargs.get('command')}"
        if call_kwargs.get("args") != ["--serve"]:
            assert False, f"args mismatch: {call_kwargs.get('args')}"
    finally:
        os.environ.pop('TEST_MCP_TOKEN', None)



# ---------------------------------------------------------------------------
# Test: Passes unresolved ${VAR} when env var is not set
# ---------------------------------------------------------------------------

def test_skips_unresolved_env_var_with_warning():
    """Does not set env value when ${VAR} env var is not set (logs warning)."""
    # Ensure the var is NOT set
    os.environ.pop('TEST_MISSING_VAR', None)

    _make_cfg(srv={
        "enabled": True,
        "transport": "stdio",
        "command": "my-tool",
        "env": {"API_KEY": "${TEST_MISSING_VAR}", "MODE": "production"},
    })

    mock_server = _make_mock_server("srv", "stdio", [{"type": "function", "function": {"name": "t1"}}])
    client = _make_mock_client({"srv": mock_server})
    g_data._registry['mcp_client'] = client

    buf, handler, old_level = _capture_logs(logging.WARNING)

    try:
        async def run():
            return await load_mcp_tools()

        result = asyncio.run(run())
        log_output = buf.getvalue()

        if len(result) != 1:
            assert False, f"expected 1 tool, got {len(result)}"
        # The resolved env should NOT contain the unresolved var
        call_kwargs = client.connect_server.call_args.kwargs
        if "API_KEY" in call_kwargs.get("env", {}):
            assert False, f"unresolved var should not be in env: {call_kwargs.get('env')}"
        if call_kwargs.get("env", {}).get("MODE") != "production":
            assert False, f"non-placeholder env vars should still be set"
        if "not set" not in log_output:
            assert False, f"missing warning log about unset var, got: {log_output}"
    finally:
        _release_logs(buf, handler, old_level)



# ---------------------------------------------------------------------------
# Test: Skips http server with no url
# ---------------------------------------------------------------------------

def test_skips_http_no_url():
    """Skips HTTP server when url is not configured (logs error)."""
    _make_cfg(srv={"enabled": True, "transport": "http"})

    client = _make_mock_client()
    g_data._registry['mcp_client'] = client

    buf, handler, old_level = _capture_logs(logging.ERROR)

    try:
        async def run():
            return await load_mcp_tools()

        result = asyncio.run(run())
        log_output = buf.getvalue()

        if result != []:
            assert False, f"expected [], got {result}"
        if "has no url configured" not in log_output:
            assert False, f"missing error log about url, got: {log_output}"
        if client.connect_server.called:
            assert False, f"connect_server should not be called"
    finally:
        _release_logs(buf, handler, old_level)



# ---------------------------------------------------------------------------
# Test: Skips sse server with no url
# ---------------------------------------------------------------------------

def test_skips_sse_no_url():
    """Skips SSE server when url is not configured (logs error)."""
    _make_cfg(srv={"enabled": True, "transport": "sse"})

    client = _make_mock_client()
    g_data._registry['mcp_client'] = client

    buf, handler, old_level = _capture_logs(logging.ERROR)

    try:
        async def run():
            return await load_mcp_tools()

        result = asyncio.run(run())
        log_output = buf.getvalue()

        if result != []:
            assert False, f"expected [], got {result}"
        if "has no url configured" not in log_output:
            assert False, f"missing error log about url, got: {log_output}"
        if client.connect_server.called:
            assert False, f"connect_server should not be called"
    finally:
        _release_logs(buf, handler, old_level)



# ---------------------------------------------------------------------------
# Test: Skips server with unknown transport
# ---------------------------------------------------------------------------

def test_skips_unknown_transport():
    """Skips server with unknown transport (logs error)."""
    _make_cfg(srv={"enabled": True, "transport": "grpc", "url": "http://x"})

    client = _make_mock_client()
    g_data._registry['mcp_client'] = client

    buf, handler, old_level = _capture_logs(logging.ERROR)

    try:
        async def run():
            return await load_mcp_tools()

        result = asyncio.run(run())
        log_output = buf.getvalue()

        if result != []:
            assert False, f"expected [], got {result}"
        if "unknown transport" not in log_output.lower():
            assert False, f"missing error log about unknown transport, got: {log_output}"
        if client.connect_server.called:
            assert False, f"connect_server should not be called"
    finally:
        _release_logs(buf, handler, old_level)



# ---------------------------------------------------------------------------
# Test: Connects stdio server with correct args and resolved env
# ---------------------------------------------------------------------------

def test_connects_stdio_server():
    """Connects stdio server with correct transport, command, args, env, reconnect."""
    _make_cfg(srv={
        "enabled": True,
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {"NODE_ENV": "production"},
        "reconnect": True,
    })

    tools = [{"type": "function", "function": {"name": "fs_read"}}]
    mock_server = _make_mock_server("srv", "stdio", tools)
    client = _make_mock_client({"srv": mock_server})
    g_data._registry['mcp_client'] = client

    async def run():
        return await load_mcp_tools()

    result = asyncio.run(run())

    if len(result) != 1:
        assert False, f"expected 1 tool, got {len(result)}"
    if result[0] != tools[0]:
        assert False, f"tool mismatch: {result[0]}"

    call_kwargs = client.connect_server.call_args.kwargs
    if call_kwargs.get("transport") != "stdio":
        assert False, f"transport: {call_kwargs.get('transport')}"
    if call_kwargs.get("command") != "npx":
        assert False, f"command: {call_kwargs.get('command')}"
    if call_kwargs.get("args") != ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]:
        assert False, f"args: {call_kwargs.get('args')}"
    if call_kwargs.get("env") != {"NODE_ENV": "production"}:
        assert False, f"env: {call_kwargs.get('env')}"
    if call_kwargs.get("reconnect") != True:
        assert False, f"reconnect: {call_kwargs.get('reconnect')}"



# ---------------------------------------------------------------------------
# Test: Connects http server with correct url
# ---------------------------------------------------------------------------

def test_connects_http_server():
    """Connects HTTP server with correct url."""
    _make_cfg(srv={
        "enabled": True,
        "transport": "http",
        "url": "http://mcp-server:8080/rpc",
    })

    tools = [{"type": "function", "function": {"name": "http_tool"}}]
    mock_server = _make_mock_server("srv", "http", tools)
    client = _make_mock_client({"srv": mock_server})
    g_data._registry['mcp_client'] = client

    async def run():
        return await load_mcp_tools()

    result = asyncio.run(run())

    if len(result) != 1:
        assert False, f"expected 1 tool, got {len(result)}"

    call_kwargs = client.connect_server.call_args.kwargs
    if call_kwargs.get("transport") != "http":
        assert False, f"transport: {call_kwargs.get('transport')}"
    if call_kwargs.get("url") != "http://mcp-server:8080/rpc":
        assert False, f"url: {call_kwargs.get('url')}"



# ---------------------------------------------------------------------------
# Test: Connects sse server with correct url
# ---------------------------------------------------------------------------

def test_connects_sse_server():
    """Connects SSE server with correct url."""
    _make_cfg(srv={
        "enabled": True,
        "transport": "sse",
        "url": "http://mcp-server:8080/sse",
    })

    tools = [{"type": "function", "function": {"name": "sse_tool"}}]
    mock_server = _make_mock_server("srv", "sse", tools)
    client = _make_mock_client({"srv": mock_server})
    g_data._registry['mcp_client'] = client

    async def run():
        return await load_mcp_tools()

    result = asyncio.run(run())

    if len(result) != 1:
        assert False, f"expected 1 tool, got {len(result)}"

    call_kwargs = client.connect_server.call_args.kwargs
    if call_kwargs.get("transport") != "sse":
        assert False, f"transport: {call_kwargs.get('transport')}"
    if call_kwargs.get("url") != "http://mcp-server:8080/sse":
        assert False, f"url: {call_kwargs.get('url')}"



# ---------------------------------------------------------------------------
# Test: Collects tools from multiple servers
# ---------------------------------------------------------------------------

def test_collects_tools_from_multiple_servers():
    """Collects tools from multiple successful servers into a single list."""
    _make_cfg(
        srv_a={
            "enabled": True,
            "transport": "stdio",
            "command": "tool-a",
        },
        srv_b={
            "enabled": True,
            "transport": "http",
            "url": "http://b:8080",
        },
    )

    tools_a = [
        {"type": "function", "function": {"name": "tool_a1"}},
        {"type": "function", "function": {"name": "tool_a2"}},
    ]
    tools_b = [
        {"type": "function", "function": {"name": "tool_b1"}},
    ]

    client = _make_mock_client({
        "srv_a": _make_mock_server("srv_a", "stdio", tools_a),
        "srv_b": _make_mock_server("srv_b", "http", tools_b),
    })
    g_data._registry['mcp_client'] = client

    async def run():
        return await load_mcp_tools()

    result = asyncio.run(run())

    if len(result) != 3:
        assert False, f"expected 3 tools, got {len(result)}"
    # Tools should be ordered by server iteration
    if result != tools_a + tools_b:
        assert False, f"unexpected tool order: {result}"
    if client.connect_server.call_count != 2:
        assert False, f"expected 2 connect_server calls, got {client.connect_server.call_count}"



# ---------------------------------------------------------------------------
# Test: Catches connection exception on one server without blocking others
# ---------------------------------------------------------------------------

def test_exception_in_one_server_doesnt_block_others():
    """Catches connection exception on one server without blocking others."""
    _make_cfg(
        bad_srv={
            "enabled": True,
            "transport": "stdio",
            "command": "bad-cmd",
        },
        good_srv={
            "enabled": True,
            "transport": "http",
            "url": "http://good:8080",
        },
    )

    tools_good = [{"type": "function", "function": {"name": "good_tool"}}]

    client = MagicMock(spec=MCPClient)
    client.servers = {}
    client.connect_server = AsyncMock()

    async def _connect(name, **kwargs):
        if name == "bad_srv":
            raise ConnectionError("Connection refused")
        return _make_mock_server(name, "http", tools_good)

    client.connect_server.side_effect = _connect
    g_data._registry['mcp_client'] = client

    buf, handler, old_level = _capture_logs(logging.ERROR)

    try:
        async def run():
            return await load_mcp_tools()

        result = asyncio.run(run())
        log_output = buf.getvalue()

        # Should still get tools from good_srv
        if len(result) != 1:
            assert False, f"expected 1 tool from good_srv, got {len(result)}"
        if result[0] != tools_good[0]:
            assert False, f"tool mismatch: {result[0]}"
        # Error should be logged for bad_srv
        if "bad_srv" not in log_output and "bad_srv" not in log_output:
            # The error might log using exc_info, check
            if "Connection refused" not in log_output and "ConnectionError" not in log_output:
                assert False, f"missing error log for bad_srv, got: {log_output}"
    finally:
        _release_logs(buf, handler, old_level)



# ---------------------------------------------------------------------------
# Test: Reuses existing MCP client from g_data
# ---------------------------------------------------------------------------

def test_reuses_existing_mcp_client():
    """Reuses MCPClient from g_data if already created."""
    _make_cfg(srv={
        "enabled": True,
        "transport": "stdio",
        "command": "echo",
    })

    tools = [{"type": "function", "function": {"name": "reused_tool"}}]
    mock_server = _make_mock_server("srv", "stdio", tools)
    client = _make_mock_client({"srv": mock_server})

    # Pre-populate g_data with client
    g_data._registry['mcp_client'] = client

    # Track whether get_or_create is called — it shouldn't because get() returns client
    original_get_or_create = g_data.get_or_create
    get_or_create_called = False

    def _tracked_get_or_create(key, factory, *a, **kw):
        nonlocal get_or_create_called
        get_or_create_called = True
        return original_get_or_create(key, factory, *a, **kw)

    # Important: g_data.get("mcp_client") returns the existing client, so
    # get_or_create should NOT be called. But the function calls it anyway
    # (line 38). Let me check the function again...
    # Line 35-38:
    #   mcp_client = g_data.get("mcp_client")
    #   if not mcp_client:
    #       mcp_client = MCPClient()
    #       g_data.get_or_create("mcp_client", lambda: mcp_client)
    # So when mcp_client IS found, get_or_create is NOT called. Good.

    async def run():
        return await load_mcp_tools()

    result = asyncio.run(run())

    if len(result) != 1:
        assert False, f"expected 1 tool, got {len(result)}"
    if result[0] != tools[0]:
        assert False, f"tool mismatch: {result[0]}"
    # Verify connect_server was called on the existing client
    if not client.connect_server.called:
        assert False, f"connect_server should be called on existing client"



# ---------------------------------------------------------------------------
# Test: g_data has no mcp_client — creates and registers one
# ---------------------------------------------------------------------------

def test_creates_new_mcp_client_when_missing():
    """Creates a new MCPClient when g_data has no mcp_client."""
    _make_cfg(srv={
        "enabled": True,
        "transport": "http",
        "url": "http://x:8080",
    })

    # No mcp_client in registry
    if "mcp_client" in g_data._registry:
        del g_data._registry["mcp_client"]

    # We need to let real MCPClient be created but mock connect_server
    # Patch MCPClient.connect_server on the class
    tools = [{"type": "function", "function": {"name": "new_client_tool"}}]

    async def _mock_connect(self, name, **kwargs):
        return _make_mock_server(name, kwargs.get("transport", "stdio"), tools)

    with patch.object(MCPClient, "connect_server", _mock_connect):
        async def run():
            return await load_mcp_tools()

        result = asyncio.run(run())

    if len(result) != 1:
        assert False, f"expected 1 tool, got {len(result)}"
    # Verify MCP client was stored in registry
    stored_client = g_data.get("mcp_client")
    if stored_client is None:
        assert False, f"mcp_client should be stored in g_data"
    if not isinstance(stored_client, MCPClient):
        assert False, f"stored client is not MCPClient: {type(stored_client)}"



# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
