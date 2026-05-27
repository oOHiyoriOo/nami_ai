# MCP Integration - Quick Start

## What is MCP?

Model Context Protocol (MCP) is a standard for connecting AI assistants to external tools and data sources. MCP servers expose tools that the AI can call, similar to local Python tools in `OllamaTools/`.

## Why Integrate MCP?

- **Extend capabilities** without writing Python code
- **Use existing MCP servers** from the community
- **Unified interface** - MCP tools work exactly like local tools
- **Easy configuration** - Enable/disable via `config.yml`

## Quick Setup

### 1. Install Node.js (for npx-based servers)

```bash
# Most MCP servers use npx
# Install Node.js if you don't have it
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

### 2. Add MCP Server to Config

Edit `config.yml`:

```yaml
mcp_servers:
  filesystem:
    enabled: true
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/workspace"]
```

### 3. Start Nami AI

```bash
python api_server.py
```

MCP tools will be loaded automatically at startup!

### 4. Verify Tools Loaded

Check logs for:
```
[INFO] Loaded 3 tools from MCP server 'filesystem'
[INFO] Total: Loaded 3 tools from 1 MCP servers
```

## Available MCP Servers

### Filesystem Access

```yaml
mcp_servers:
  filesystem:
    enabled: true
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
```

**Tools exposed:**
- `mcp_filesystem_read_file` - Read file contents
- `mcp_filesystem_write_file` - Write to file
- `mcp_filesystem_list_directory` - List directory contents

### GitHub Integration

```yaml
mcp_servers:
  github:
    enabled: true
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
```

**Tools exposed:**
- `mcp_github_create_issue`
- `mcp_github_search_repos`
- `mcp_github_create_pull_request`
- And more...

### Database Access (PostgreSQL)

```yaml
mcp_servers:
  postgres:
    enabled: true
    command: uvx
    args: ["mcp-server-postgres", "postgresql://user:pass@localhost/dbname"]
```

**Tools exposed:**
- `mcp_postgres_query` - Execute SQL queries
- `mcp_postgres_list_tables`
- And more...

## Usage Example

Once configured, the AI can use MCP tools naturally:

**User:** "Read the file /workspace/config.json"

**AI:** *Calls `mcp_filesystem_read_file` with `{path: "/workspace/config.json"}`*

**Result:** File contents returned to AI, who can then discuss/modify them

## Testing

Test MCP integration without running the full app:

```bash
python test_mcp.py
```

This will:
1. Load your MCP server configuration
2. Connect to enabled servers
3. List available tools
4. Show any connection errors

## Troubleshooting

### "MCP server not found"

Install the server package first:

```bash
# For @modelcontextprotocol servers
npx -y @modelcontextprotocol/server-filesystem /tmp

# For uvx servers
uvx mcp-server-postgres postgresql://localhost/test
```

### "Environment variable not set"

Export required environment variables:

```bash
export GITHUB_TOKEN="your_token_here"
```

Or use `.env` file (add support in config loader if needed).

### "Server crashed immediately"

Check server arguments are correct:

```bash
# Test server manually
npx -y @modelcontextprotocol/server-filesystem /workspace

# Should stay running and wait for JSON-RPC input
```

### "No tools loaded"

Check logs for specific error messages:

```bash
python api_server.py 2>&1 | grep -i mcp
```

## Architecture

```
Your AI
   ↓
Nami AI (api_server.py)
   ↓
Tool Execution
   ↓
   ├─ Local Tools (OllamaTools/*.py)
   └─ MCP Tools (via mcp_client.py)
       ↓
   MCP Server Process (npx/uvx)
       ↓
   External Service (filesystem, GitHub, etc.)
```

## Next Steps

- **Explore servers**: https://github.com/modelcontextprotocol/servers
- **Read full guide**: `docs/guides/mcp-integration.md`
- **Check architecture**: `docs/guides/mcp-architecture.txt`
- **Build custom server**: https://modelcontextprotocol.io/docs/building-servers

## Common Patterns

### Multiple Servers

```yaml
mcp_servers:
  filesystem:
    enabled: true
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
  
  github:
    enabled: true
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
  
  postgres:
    enabled: true
    command: uvx
    args: ["mcp-server-postgres", "postgresql://localhost/mydb"]
```

All tools from all servers are available simultaneously!

### Disable Server Temporarily

```yaml
mcp_servers:
  filesystem:
    enabled: false  # Just toggle this
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
```

### Environment Variables

```yaml
mcp_servers:
  custom:
    enabled: true
    command: python
    args: ["my_mcp_server.py"]
    env:
      API_KEY: "${MY_API_KEY}"
      DATABASE_URL: "${DATABASE_URL}"
```

Variables resolved from shell environment at runtime.
