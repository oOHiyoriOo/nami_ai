"""
MCP client for connecting to Model Context Protocol servers.
Provides a unified interface for calling tools on remote MCP servers.

Supports three transports:
- stdio: subprocess with stdin/stdout JSON-RPC (default)
- http:  JSON-RPC over HTTP POST to a remote URL
- sse:   JSON-RPC over HTTP POST with Server-Sent Events response
"""
import asyncio
import json
import logging
import os
from typing import Any
from dataclasses import dataclass, field

STDERR_BUFFER_MAX = 100  # lines

# Safe environment variables allowed through to MCP server subprocesses.
# Only explicitly whitelisted vars are forwarded — secrets like NEO4J_PASSWORD,
# DISCORD_TOKEN, and SANDBOX_PASSWORD are never leaked to child processes.
_SAFE_ENV_VARS = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
    "TMPDIR", "TMP", "TEMP",
    "TERM", "COLORTERM",
    "PWD", "OLDPWD",
})


@dataclass
class MCPServer:
    """Represents an MCP server connection — transport-agnostic."""
    name: str
    transport: str  # "stdio", "http", "sse"
    tools: list[dict]
    # stdio-specific (None for http/sse)
    process: asyncio.subprocess.Process | None = None
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    stderr_buffer: list[str] = field(default_factory=list)
    _stderr_task: "asyncio.Task | None" = field(default=None, repr=False)
    # http/sse-specific (None for stdio)
    url: str | None = None
    _exit_stack: "AsyncExitStack | None" = field(default=None, repr=False)
    _session: Any = field(default=None, repr=False)
    # reconnect support (stored for auto-reconnect on process crash)
    _command: str | None = field(default=None, repr=False)
    _args: list[str] | None = field(default=None, repr=False)
    _env: dict[str, str] | None = field(default=None, repr=False)
    _reconnect: bool = field(default=False, repr=False)
    _monitor_task: "asyncio.Task | None" = field(default=None, repr=False)


class MCPClient:
    """Manages connections to MCP servers across stdio, HTTP, and SSE transports."""

    def __init__(self):
        self.servers: dict[str, MCPServer] = {}
        self._message_id = 2

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect_server(
        self,
        name: str,
        transport: str = "stdio",
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        reconnect: bool = False,
        cwd: str | None = None,
    ) -> MCPServer:
        """
        Connect to an MCP server.

        Args:
            name: Server identifier.
            transport: ``"stdio"`` (default), ``"http"``, or ``"sse"``.
            command: Executable path (stdio only).
            args: Command arguments (stdio only).
            env: Extra environment variables (stdio only).
            url: Server URL (http/sse only).
            reconnect: If True, auto-reconnect on process crash (stdio only).
            cwd: Working directory for the subprocess (stdio only).

        Returns:
            MCPServer instance.
        """
        if transport == "stdio":
            return await self._connect_server_stdio(name, command, args, env, reconnect, cwd)
        elif transport == "http":
            return await self._connect_server_http(name, url, reconnect)
        elif transport == "sse":
            return await self._connect_server_sse(name, url, reconnect)
        else:
            raise ValueError(f"Unknown transport: {transport}")

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> Any:
        """
        Call a tool on an MCP server.

        Args:
            server_name: Name of the MCP server.
            tool_name: Name of the tool to call (without mcp_ prefix).
            arguments: Tool arguments.

        Returns:
            Tool result.
        """
        server = self.servers.get(server_name)
        if not server:
            raise ValueError(f"MCP server '{server_name}' not connected")

        if server.transport == "stdio":
            return await self._call_tool_stdio(server, tool_name, arguments)
        elif server.transport in ("http", "sse"):
            return await self._call_tool_http(server, tool_name, arguments)
        else:
            raise ValueError(f"Unknown transport: {server.transport}")

    # ------------------------------------------------------------------
    # stdio transport (original subprocess-based implementation)
    # ------------------------------------------------------------------

    async def _connect_server_stdio(
        self,
        name: str,
        command: str | None,
        args: list[str] | None,
        env: dict[str, str] | None,
        reconnect: bool = False,
        cwd: str | None = None,
    ) -> MCPServer:
        """Start an MCP server subprocess and initialize via JSON-RPC over stdio."""
        if not command:
            raise ValueError(f"MCP server '{name}' has no command configured")
        if args is None:
            args = []

        logging.info(f"Starting MCP server '{name}': {command} {' '.join(args)}")

        # Prepare environment — only forward whitelisted vars to prevent
        # secrets (NEO4J_PASSWORD, DISCORD_TOKEN, etc.) leaking to child processes.
        server_env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_VARS}
        if env:
            server_env.update(env)

        # Start subprocess with stdio transport
        process = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=server_env,
            cwd=cwd,
        )

        if not process.stdin or not process.stdout:
            raise RuntimeError(f"Failed to create pipes for MCP server '{name}'")

        reader = process.stdout
        writer = process.stdin

        # Start reading stderr in background
        server_stderr_buffer: list[str] = []
        stderr_task: asyncio.Task | None = None
        if process.stderr:
            stderr_task = asyncio.create_task(
                self._read_stderr(name, process.stderr, server_stderr_buffer)
            )

        # Initialize MCP connection
        await self._send_message(writer, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "nami-ai",
                    "version": "1.0.0"
                }
            }
        })

        init_response = await self._read_message(reader)

        if "error" in init_response:
            raise Exception(f"MCP server initialization failed: {init_response['error']}")
        
        # Send initialized notification per MCP spec
        await self._send_message(writer, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        
        logging.info(f"MCP server '{name}' initialized")

        # List available tools
        await self._send_message(writer, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        })

        tools_response = await self._read_message(reader)

        if "error" in tools_response:
            raise Exception(f"Failed to list tools: {tools_response['error']}")

        mcp_tools = tools_response.get('result', {}).get('tools', [])
        tools = self._convert_mcp_tools(name, mcp_tools)

        server = MCPServer(
            name=name,
            transport="stdio",
            process=process,
            reader=reader,
            writer=writer,
            tools=tools,
            stderr_buffer=server_stderr_buffer,
            _stderr_task=stderr_task,
            _command=command,
            _args=args,
            _env=env,
            _reconnect=reconnect,
        )

        self.servers[name] = server
        logging.info(f"Loaded {len(tools)} tools from MCP server '{name}'")

        # Start background health monitor (watches process.wait())
        server._monitor_task = asyncio.create_task(
            self._monitor_server(name, server)
        )

        return server

    async def _call_tool_stdio(
        self, server: MCPServer, tool_name: str, arguments: dict
    ) -> Any:
        """Call a tool on a stdio-connected MCP server via JSON-RPC."""
        msg_id = self._next_id()

        await self._send_message(server.writer, {  # type: ignore[arg-type]
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        })

        response = await self._read_message(server.reader)  # type: ignore[arg-type]

        if "error" in response:
            error_msg = response['error'].get('message', str(response['error']))
            raise Exception(f"MCP tool error: {error_msg}")

        return self._extract_result(response)

    # ------------------------------------------------------------------
    # HTTP / SSE transport (JSON-RPC over HTTP POST)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_initialize_request(msg_id: int) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nami-ai", "version": "1.0.0"},
            },
        }

    @staticmethod
    def _build_tools_list_request(msg_id: int) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/list"}

    @staticmethod
    def _build_tools_call_request(msg_id: int, tool_name: str, arguments: dict) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

    async def _connect_server_http(
        self, name: str, url: str | None, reconnect: bool = False,
    ) -> MCPServer:
        """Connect to an MCP server via HTTP JSON-RPC POST."""
        if not url:
            raise ValueError(f"MCP HTTP server '{name}' has no URL configured")

        import aiohttp
        session = aiohttp.ClientSession()

        try:
            # Initialize
            init_id = self._next_id()
            async with session.post(url, json=self._build_initialize_request(init_id),
                                     timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(
                        f"MCP HTTP server '{name}' returned {resp.status}: {body[:200]}"
                    )
                init_response = await resp.json()

            if "error" in init_response:
                raise Exception(
                    f"MCP server initialization failed: {init_response['error']}"
                )

            logging.info(f"MCP HTTP server '{name}' initialized")

            # List tools
            tools_id = self._next_id()
            async with session.post(url, json=self._build_tools_list_request(tools_id),
                                     timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(
                        f"MCP HTTP server '{name}' tools/list returned {resp.status}: {body[:200]}"
                    )
                tools_response = await resp.json()

            if "error" in tools_response:
                raise Exception(f"Failed to list tools: {tools_response['error']}")

            mcp_tools = tools_response.get("result", {}).get("tools", [])
            tools = self._convert_mcp_tools(name, mcp_tools)

            server = MCPServer(
                name=name,
                transport="http",
                tools=tools,
                url=url,
                _session=session,
                _reconnect=reconnect,
            )
            self.servers[name] = server
            logging.info(
                f"Loaded {len(tools)} tools from MCP HTTP server '{name}'"
            )
            return server

        except Exception:
            await session.close()
            raise

    async def _connect_server_sse(
        self, name: str, url: str | None, reconnect: bool = False,
    ) -> MCPServer:
        """Connect to an MCP server via SSE (HTTP POST → SSE stream).

        For initialization and tool listing we use the same HTTP POST approach
        as the http transport.  The SSE streaming applies to tool *calls*, which
        are handled in ``_call_tool_http`` based on the transport type.
        """
        if not url:
            raise ValueError(f"MCP SSE server '{name}' has no URL configured")

        import aiohttp
        session = aiohttp.ClientSession()

        try:
            # Initialize (same POST as HTTP)
            init_id = self._next_id()
            async with session.post(url, json=self._build_initialize_request(init_id),
                                     timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(
                        f"MCP SSE server '{name}' returned {resp.status}: {body[:200]}"
                    )
                init_response = await resp.json()

            if "error" in init_response:
                raise Exception(
                    f"MCP server initialization failed: {init_response['error']}"
                )

            logging.info(f"MCP SSE server '{name}' initialized")

            # List tools
            tools_id = self._next_id()
            async with session.post(url, json=self._build_tools_list_request(tools_id),
                                     timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(
                        f"MCP SSE server '{name}' tools/list returned {resp.status}: {body[:200]}"
                    )
                tools_response = await resp.json()

            if "error" in tools_response:
                raise Exception(f"Failed to list tools: {tools_response['error']}")

            mcp_tools = tools_response.get("result", {}).get("tools", [])
            tools = self._convert_mcp_tools(name, mcp_tools)

            server = MCPServer(
                name=name,
                transport="sse",
                tools=tools,
                url=url,
                _session=session,
                _reconnect=reconnect,
            )
            self.servers[name] = server
            logging.info(
                f"Loaded {len(tools)} tools from MCP SSE server '{name}'"
            )
            return server

        except Exception:
            await session.close()
            raise

    @staticmethod
    async def _parse_sse_stream(resp) -> dict:
        """Parse a Server-Sent Events stream and return the final result JSON."""
        result_text: str | None = None
        async for line in resp.content:
            decoded = line.decode("utf-8").rstrip("\n").rstrip("\r")
            if decoded.startswith("data: "):
                data_str = decoded[6:]
                # Collect the last data event as the result
                result_text = data_str
            elif decoded == "" and result_text is not None:
                # Empty line signals end of event — but continue for more events
                pass
        if result_text is None:
            raise Exception("MCP SSE stream closed without delivering a result")
        return json.loads(result_text)

    async def _call_tool_http(
        self, server: MCPServer, tool_name: str, arguments: dict
    ) -> Any:
        """Call a tool on an HTTP/SSE-connected MCP server."""
        import aiohttp
        session: aiohttp.ClientSession = server._session  # type: ignore[assignment]
        msg_id = self._next_id()

        if server.transport == "sse":
            # SSE: POST with Accept: text/event-stream
            headers = {"Accept": "text/event-stream"}
            async with session.post(
                server.url,
                json=self._build_tools_call_request(msg_id, tool_name, arguments),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(
                        f"MCP SSE tool error ({resp.status}): {body[:200]}"
                    )
                response = await self._parse_sse_stream(resp)
        else:
            # Plain HTTP: POST → JSON
            async with session.post(
                server.url,
                json=self._build_tools_call_request(msg_id, tool_name, arguments),
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(
                        f"MCP HTTP tool error ({resp.status}): {body[:200]}"
                    )
                response = await resp.json()

        if "error" in response:
            error_msg = response["error"].get("message", str(response["error"]))
            raise Exception(f"MCP tool error: {error_msg}")

        return self._extract_result(response)

    @staticmethod
    def _extract_result(response: dict) -> str:
        """Extract text content from a JSON-RPC response result."""
        result = response.get("result", {})
        content_list = result.get("content", [])
        text_parts = [
            c.get("text", "") for c in content_list if c.get("type") == "text"
        ]
        return "\n".join(text_parts) if text_parts else str(result)

    # ------------------------------------------------------------------
    # Tool conversion (shared across transports)
    # ------------------------------------------------------------------

    def _convert_mcp_tools(self, server_name: str, mcp_tools: list[dict]) -> list[dict]:
        """
        Convert MCP tool definitions to Nami AI tool format.

        Args:
            server_name: Name of the MCP server.
            mcp_tools: List of MCP tool definitions.

        Returns:
            List of Nami AI tool definitions.
        """
        converted = []

        for mcp_tool in mcp_tools:
            tool_name = mcp_tool['name']

            def make_wrapper(server: str, tool: str):
                async def mcp_tool_wrapper(**kwargs):
                    """Wrapper that calls MCP server tool."""
                    from lib.global_registry import g_data
                    from OllamaTools import tool_success, tool_error

                    try:
                        mcp_client = g_data.get("mcp_client")
                        if not mcp_client:
                            return tool_error("MCP client not initialized")

                        result = await mcp_client.call_tool(server, tool, kwargs)
                        return tool_success(result, server=server, tool=tool)

                    except Exception as e:
                        logging.error(f"MCP tool error ({server}/{tool}): {e}")
                        return tool_error(str(e), server=server, tool=tool)

                return mcp_tool_wrapper

            converted.append({
                "type": "function",
                "function": {
                    "name": f"mcp_{server_name}_{tool_name}",
                    "description": mcp_tool.get('description', f'MCP tool: {tool_name}'),
                    "parameters": mcp_tool.get('inputSchema', {"type": "object", "properties": {}})
                },
                "func": make_wrapper(server_name, tool_name),
                "_mcp_server": server_name,
                "_mcp_tool_name": tool_name
            })

        return converted

    # ------------------------------------------------------------------
    # stdio helpers
    # ------------------------------------------------------------------

    async def _send_message(self, writer: asyncio.StreamWriter, message: dict):
        """Send JSON-RPC message to MCP server."""
        data = json.dumps(message) + "\n"
        writer.write(data.encode())
        await writer.drain()

    async def _read_message(self, reader: asyncio.StreamReader) -> dict:
        """Read JSON-RPC message from MCP server."""
        line = await reader.readline()
        if not line:
            raise EOFError("MCP server closed connection")
        return json.loads(line.decode())

    def _next_id(self) -> int:
        """Generate next message ID."""
        self._message_id += 1
        return self._message_id

    async def _read_stderr(self, name: str, stderr: asyncio.StreamReader, buffer: list[str]):
        """Background task: read and log stderr lines from an MCP server process."""
        while True:
            try:
                line = await stderr.readline()
            except Exception:
                break
            if not line:
                break
            decoded = line.decode().rstrip()
            buffer.append(decoded)
            if len(buffer) > STDERR_BUFFER_MAX:
                buffer.pop(0)
            logging.warning("[mcp:%s] stderr: %s", name, decoded)

    # ------------------------------------------------------------------
    # Health monitoring & auto-reconnect
    # ------------------------------------------------------------------

    async def _monitor_server(self, name: str, server: MCPServer):
        """Watch for process exit and attempt reconnection (stdio only)."""
        if server.transport != "stdio" or server.process is None:
            return

        await server.process.wait()
        returncode = server.process.returncode
        logging.warning(
            f"MCP server '{name}' exited with code {returncode}"
        )

        # Remove dead server so call_tool returns a clear error
        self.servers.pop(name, None)

        # Attempt reconnect if configured
        if server._reconnect:
            logging.info(f"Attempting to reconnect MCP server '{name}'...")
            try:
                await self.connect_server(
                    name,
                    transport=server.transport,
                    command=server._command,
                    args=server._args,
                    env=server._env,
                    reconnect=True,
                )
            except Exception as e:
                logging.error(
                    f"Failed to reconnect MCP server '{name}': {e}"
                )

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    async def disconnect_all(self):
        """Disconnect all MCP servers."""
        for name, server in list(self.servers.items()):
            await self.disconnect_server(name)

    async def disconnect_server(self, name: str):
        """Disconnect a specific MCP server."""
        server = self.servers.get(name)
        if not server:
            return

        if server.transport == "stdio":
            await self._disconnect_stdio(server, name)
        elif server.transport in ("http", "sse"):
            await self._disconnect_http(server, name)
        else:
            logging.warning(f"Unknown transport '{server.transport}' for server '{name}'")
            del self.servers[name]

    async def _disconnect_stdio(self, server: MCPServer, name: str):
        """Terminate a stdio subprocess and clean up."""
        # Cancel monitor task first (so it doesn't race with disconnect)
        if server._monitor_task and not server._monitor_task.done():
            server._monitor_task.cancel()
            try:
                await server._monitor_task
            except asyncio.CancelledError:
                pass

        # Cancel stderr reader task
        if server._stderr_task and not server._stderr_task.done():
            server._stderr_task.cancel()
            try:
                await server._stderr_task
            except asyncio.CancelledError:
                pass

        try:
            if server.writer:
                server.writer.close()
                await server.writer.wait_closed()
            if server.process:
                server.process.terminate()
                await asyncio.wait_for(server.process.wait(), timeout=5.0)
            logging.info(f"Disconnected MCP server '{name}'")
        except asyncio.TimeoutError:
            logging.warning(f"MCP server '{name}' didn't terminate, forcing...")
            if server.process:
                server.process.kill()
                await server.process.wait()
        except Exception as e:
            logging.error(f"Error disconnecting MCP server '{name}': {e}")
        finally:
            del self.servers[name]

    async def _disconnect_http(self, server: MCPServer, name: str):
        """Close an HTTP/SSE session."""
        if server._monitor_task and not server._monitor_task.done():
            server._monitor_task.cancel()
            try:
                await server._monitor_task
            except asyncio.CancelledError:
                pass

        session = getattr(server, "_session", None)
        if session:
            await session.close()
        del self.servers[name]
        logging.info(f"Disconnected MCP server '{name}'")
