"""
nami_reload_tools — Hot-reload all tools without server restart.

Publishes ``system.reload_tools`` on the EventBus, which triggers
``_on_reload_tools`` in app_initializer to re-run ToolContext.for_chat()
and update the global tool registry.
"""

from lib.global_registry import g_data
from lib.services.event_bus import Event
from OllamaTools import tool_error, tool_success


async def nami_reload_tools() -> str:
    """Hot-reload all tools by publishing a system.reload_tools event.

    This activates any tool file edits without requiring a full server restart.
    Also useful after MCP server reconnects or toktoken index updates.
    """
    event_bus = g_data.get("event_bus")
    if not event_bus:
        return tool_error("EventBus not available — server may not be fully initialized")

    await event_bus.publish(Event(type="system.reload_tools", data={
        "trigger": "nami_reload_tools",
    }))
    return tool_success("Tools reloaded successfully")


def get_tool() -> list[dict]:
    return [{
        "type": "function",
        "safe": True,
        "categories": ["self_modification"],
        "function": {
            "name": "nami_reload_tools",
            "description": (
                "Hot-reload all tools without restarting the server. "
                "Use this after editing tool files (via nami_edit_code if auto_reload was disabled) "
                "or after MCP server reconnects to activate new tool definitions."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        "func": nami_reload_tools,
    }]
