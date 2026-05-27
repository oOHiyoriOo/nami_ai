# MCP Server Integration

## Overview

Nami AI integrates Model Context Protocol (MCP) servers as remote tool providers. MCP servers are loaded and managed alongside local tools in `OllamaTools/`, and are fully transparent to AI providers — they use the same tool schema format.

## Architecture

### Current Tool System

**Local Tools** (`OllamaTools/`):
- Each tool is a Python module exporting `get_tool()` function
- Returns dict with `type`, `function` schema, and `func` reference
- Loaded by `DynamicLoader` → stored in `g_data["tools"]`
- Executed through the shared `lib/services/tool_executor.py` loop via the tool's `func` wrapper

### MCP Integration

**MCP Tools** (via MCP servers):
- MCP server connections defined in `config.yml`
- Each server exposes multiple tools via MCP protocol
- Tools loaded at startup, registered in `g_data["tools"]` alongside local tools
- Executed via MCP client calling remote server
- Transparent to AI providers - same tool schema format

## Implementation Plan

### 1. Configuration (`config.yml`)

```yaml
# MCP Server configurations
mcp_servers:
  # Filesystem operations
  filesystem:
    enabled: true
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/workspace"]
    
  # GitHub integration
  github:
    enabled: true
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
  
  # Database access
  postgres:
    enabled: false
    command: uvx
    args: ["mcp-server-postgres", "postgresql://localhost/mydb"]
```

### 2. MCP Client Library

Create `lib/mcp_client.py`:

```python
"""MCP client for connecting to MCP servers."""
import asyncio
import json
import logging
from typing import Any
from dataclasses import dataclass


@dataclass
class MCPServer:
    """Represents an MCP server connection."""
    name: str
    process: asyncio.subprocess.Process
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    tools: list[dict]


class MCPClient:
    """Manages connections to MCP servers."""
    
    def __init__(self):
        self.servers: dict[str, MCPServer] = {}
    
    async def connect_server(self, name: str, command: str, args: list[str], env: dict[str, str] | None = None) -> MCPServer:
        """
        Start an MCP server process and initialize connection.
        
        Args:
            name: Server identifier
            command: Command to run (e.g., 'npx', 'uvx')
            args: Command arguments
            env: Environment variables
        
        Returns:
            MCPServer instance
        """
        # Start subprocess with stdio transport
        process = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **(env or {})}
        )
        
        reader = process.stdout
        writer = process.stdin
        
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
        logging.info(f"MCP server '{name}' initialized: {init_response}")
        
        # List available tools
        await self._send_message(writer, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        })
        
        tools_response = await self._read_message(reader)
        tools = self._convert_mcp_tools(name, tools_response.get('result', {}).get('tools', []))
        
        server = MCPServer(
            name=name,
            process=process,
            reader=reader,
            writer=writer,
            tools=tools
        )
        
        self.servers[name] = server
        logging.info(f"Loaded {len(tools)} tools from MCP server '{name}'")
        
        return server
    
    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> Any:
        """
        Call a tool on an MCP server.
        
        Args:
            server_name: Name of the MCP server
            tool_name: Name of the tool to call
            arguments: Tool arguments
        
        Returns:
            Tool result
        """
        server = self.servers.get(server_name)
        if not server:
            raise ValueError(f"MCP server '{server_name}' not connected")
        
        await self._send_message(server.writer, {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        })
        
        response = await self._read_message(server.reader)
        
        if "error" in response:
            raise Exception(f"MCP tool error: {response['error']}")
        
        return response.get('result', {}).get('content', [])
    
    def _convert_mcp_tools(self, server_name: str, mcp_tools: list[dict]) -> list[dict]:
        """
        Convert MCP tool definitions to Nami AI tool format.
        
        Args:
            server_name: Name of the MCP server
            mcp_tools: List of MCP tool definitions
        
        Returns:
            List of Nami AI tool definitions
        """
        converted = []
        
        for mcp_tool in mcp_tools:
            # Create wrapper function for this MCP tool
            async def mcp_tool_wrapper(
                _server=server_name,
                _tool_name=mcp_tool['name'],
                **kwargs
            ):
                """Wrapper that calls MCP server tool."""
                from lib.global_registry import g_data
                mcp_client = g_data.get("mcp_client")
                result = await mcp_client.call_tool(_server, _tool_name, kwargs)
                
                # Format result similar to local tools
                from OllamaTools import tool_success
                return tool_success(result)
            
            # Convert to Nami AI tool format
            converted.append({
                "type": "function",
                "function": {
                    "name": f"mcp_{server_name}_{mcp_tool['name']}",  # Prefix to avoid collisions
                    "description": mcp_tool.get('description', ''),
                    "parameters": mcp_tool.get('inputSchema', {})
                },
                "func": mcp_tool_wrapper,
                "_mcp_server": server_name,
                "_mcp_tool_name": mcp_tool['name']
            })
        
        return converted
    
    async def _send_message(self, writer: asyncio.StreamWriter, message: dict):
        """Send JSON-RPC message to MCP server."""
        data = json.dumps(message) + "\n"
        writer.write(data.encode())
        await writer.drain()
    
    async def _read_message(self, reader: asyncio.StreamReader) -> dict:
        """Read JSON-RPC message from MCP server."""
        line = await reader.readline()
        return json.loads(line.decode())
    
    def _next_id(self) -> int:
        """Generate next message ID."""
        if not hasattr(self, '_message_id'):
            self._message_id = 2
        self._message_id += 1
        return self._message_id
    
    async def disconnect_all(self):
        """Disconnect all MCP servers."""
        for name, server in self.servers.items():
            try:
                server.process.terminate()
                await server.process.wait()
                logging.info(f"Disconnected MCP server '{name}'")
            except Exception as e:
                logging.error(f"Error disconnecting MCP server '{name}': {e}")
```

### 3. MCP Tool Loader

Create `lib/utils/mcp_loader.py`:

```python
"""MCP tool loader - loads tools from MCP servers."""
import logging
from lib.mcp_client import MCPClient
from lib.global_registry import g_data


async def load_mcp_tools() -> list[dict]:
    """
    Load tools from configured MCP servers.
    
    Returns:
        List of tool definitions from all MCP servers
    """
    config = g_data.get("cfg")
    mcp_config = config.data.get('mcp_servers', {})
    
    if not mcp_config:
        logging.info("No MCP servers configured")
        return []
    
    # Create MCP client and store in global registry
    mcp_client = MCPClient()
    g_data.get_or_create("mcp_client", lambda: mcp_client)
    
    all_tools = []
    
    for server_name, server_config in mcp_config.items():
        if not server_config.get('enabled', False):
            logging.info(f"MCP server '{server_name}' is disabled")
            continue
        
        try:
            command = server_config.get('command')
            args = server_config.get('args', [])
            env = server_config.get('env', {})
            
            server = await mcp_client.connect_server(server_name, command, args, env)
            all_tools.extend(server.tools)
            
        except Exception as e:
            logging.error(f"Failed to load MCP server '{server_name}': {e}")
    
    logging.info(f"Loaded {len(all_tools)} tools from {len(mcp_client.servers)} MCP servers")
    return all_tools
```

### 4. Update App Initializer

Modify `lib/services/app_initializer.py`:

```python
async def _initialize_tools(self):
    """Load tools from both local modules and MCP servers."""
    # Load local tools
    local_tools = await load_tools(None)
    
    # Load MCP tools
    from lib.utils.mcp_loader import load_mcp_tools
    mcp_tools = await load_mcp_tools()
    
    # Combine both
    all_tools = local_tools + mcp_tools
    g_data.get_or_create("tools", lambda: all_tools)
    
    logging.info(f"Loaded {len(local_tools)} local tools and {len(mcp_tools)} MCP tools")
```

### 5. Cleanup on Shutdown

Update `api_server.py` lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan."""
    await initializer.initialize()
    yield
    
    # Cleanup
    mcp_client = g_data.get("mcp_client")
    if mcp_client:
        await mcp_client.disconnect_all()
    
    await initializer.cleanup()
```

## Tool Naming Convention

To avoid name collisions between local tools and MCP tools:

- **Local tools**: Keep original names (e.g., `search_web`, `search_memory`)
- **MCP tools**: Prefix with `mcp_<server>_` (e.g., `mcp_filesystem_read_file`, `mcp_github_create_issue`)

The AI will see all tools in its schema and can call any of them.

## Example Usage

### Configuration

```yaml
mcp_servers:
  filesystem:
    enabled: true
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/workspace"]
```

### Tool Discovery

At startup:
1. App initializer loads local tools from `OllamaTools/`
2. MCP loader connects to configured servers
3. Each MCP server provides its tool list
4. Tools converted to Nami AI format and registered
5. All tools available to AI providers

### Tool Execution

AI receives tool schema:
```json
{
  "name": "mcp_filesystem_read_file",
  "description": "Read contents of a file",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {"type": "string"}
    }
  }
}
```

AI calls tool → Wrapper function → MCP client → MCP server → Result returned

## Benefits

1. **Unified Interface**: MCP tools and local tools use same execution path
2. **No Provider Changes**: AI providers don't need to know about MCP
3. **Standard Loading**: Uses existing `DynamicLoader` pattern
4. **Transparent Execution**: Tool execution logic unchanged
5. **Easy Configuration**: Enable/disable servers via `config.yml`

## Future Enhancements

1. **Tool Filtering**: Allow filtering which MCP tools to expose
2. **Tool Aliases**: Map MCP tool names to custom names
3. **Hot Reload**: Reconnect to MCP servers without restarting
4. **Health Checks**: Monitor MCP server status and reconnect if needed
5. **Tool Categories**: Group tools by server/category in tool list
6. **Streaming Support**: Handle streaming responses from MCP tools
7. **Resource Support**: Add MCP resource support (prompts, templates)

## Migration Path

1. **Phase 1**: Implement basic MCP client and loader
2. **Phase 2**: Add to app initializer, test with filesystem server
3. **Phase 3**: Add error handling, reconnection logic
4. **Phase 4**: Add configuration validation and documentation
5. **Phase 5**: Add advanced features (streaming, resources, etc.)
