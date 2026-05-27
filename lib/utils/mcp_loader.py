"""
MCP tool loader - loads tools from configured MCP servers.
Integrates MCP servers into the existing tool loading system.

Supports three transports:
- stdio: subprocess (command/args/env config keys)
- http:  HTTP JSON-RPC (url config key)
- sse:   SSE JSON-RPC (url config key)
"""
import os
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
    if not config:
        logging.warning("Configuration not available, skipping MCP tools")
        return []

    mcp_config = config.data.get('mcp_servers', {})

    if not mcp_config:
        logging.info("No MCP servers configured")
        return []

    # Create MCP client and store in global registry
    mcp_client = g_data.get("mcp_client")
    if not mcp_client:
        mcp_client = MCPClient()
        g_data.get_or_create("mcp_client", lambda: mcp_client)

    all_tools = []

    for server_name, server_config in mcp_config.items():
        if not server_config.get('enabled', False):
            logging.info(f"MCP server '{server_name}' is disabled")
            continue

        try:
            transport = server_config.get('transport', 'stdio')
            reconnect = server_config.get('reconnect', False)

            if transport == 'stdio':
                command = server_config.get('command')
                if not command:
                    logging.error(f"MCP server '{server_name}' has no command configured")
                    continue

                args = server_config.get('args', [])
                env = server_config.get('env', {})
                cwd = server_config.get('cwd')

                # Resolve environment variable placeholders
                resolved_env = {}
                for key, value in env.items():
                    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                        env_var = value[2:-1]
                        resolved_value = os.getenv(env_var)
                        if resolved_value:
                            resolved_env[key] = resolved_value
                        else:
                            logging.warning(
                                f"Environment variable {env_var} not set for MCP server '{server_name}'"
                            )
                    else:
                        resolved_env[key] = value

                server = await mcp_client.connect_server(
                    server_name, transport="stdio",
                    command=command, args=args, env=resolved_env,
                    reconnect=reconnect, cwd=cwd,
                )

            elif transport in ("http", "sse"):
                url = server_config.get('url')
                if not url:
                    logging.error(
                        f"MCP {transport} server '{server_name}' has no url configured"
                    )
                    continue

                server = await mcp_client.connect_server(
                    server_name, transport=transport, url=url,
                    reconnect=reconnect,
                )

            else:
                logging.error(
                    f"MCP server '{server_name}': unknown transport '{transport}'"
                )
                continue

            all_tools.extend(server.tools)
            logging.info(
                f"Successfully loaded {len(server.tools)} tools "
                f"from MCP server '{server_name}' ({transport})"
            )

        except Exception as e:
            logging.error(
                f"Failed to load MCP server '{server_name}': {e}", exc_info=True
            )

    logging.info(
        f"Total: Loaded {len(all_tools)} tools from "
        f"{len(mcp_client.servers)} MCP servers"
    )
    return all_tools
