import os
import importlib.util
import logging
import asyncio
import discord

# Load tools from the "OllamaTools" folder with dynamic dependency injection
async def load_tools(client: discord.Client):
    tools = []
    tools_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "OllamaTools")

    if os.path.exists(tools_path):
        for filename in await asyncio.to_thread(os.listdir, tools_path):
            if filename.endswith(".py"):
                tool_path = os.path.join(tools_path, filename)
                spec = importlib.util.spec_from_file_location("module.name", tool_path)
                tool_module = importlib.util.module_from_spec(spec)
                await asyncio.to_thread(spec.loader.exec_module, tool_module)
                if hasattr(tool_module, "get_tool"):
                    tool = tool_module.get_tool()

                    if isinstance(tool, dict) and tool['type'] == "function":
                        tools.append({
                            "type": "function",
                            "function": {
                                "name": tool['function']['name'],
                                "description": tool['function']['description'],
                                "parameters": tool['function']['parameters'],
                            },
                            "func": tool_module.__dict__[tool['function']['name']],
                        })
                        
                        logging.info(f"Loaded tool: {tool['function']['name']}")

    return tools