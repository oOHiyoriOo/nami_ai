"""
retrieve_tool_response.py — Expose ToolResponseLog.get() to the AI model.

The AI sees [TOOL_RESPONSE:<uuid>] placeholders in history from previous
tool calls. This tool lets it expand any placeholder by looking up the
stored response in the ToolResponseLog.
"""

import logging

from lib.global_registry import g_data
from OllamaTools import tool_error, tool_success

logger = logging.getLogger(__name__)


async def retrieve_tool_response(uuid: str) -> str:
    """
    Retrieve a stored tool response by its UUID.

    Args:
        uuid: The UUID from a [TOOL_RESPONSE:<uuid>] placeholder seen in history.

    Returns:
        The stored response data (tool_name, response_text, metadata, timestamp).
    """
    log = g_data.get("tool_response_log")
    if not log:
        return tool_error("Tool response log not available", uuid=uuid)

    try:
        record = await log.get(uuid)
    except Exception as e:
        logger.error("ToolResponseLog.get(%s) failed: %s", uuid, e)
        return tool_error(f"Failed to retrieve tool response: {e}", uuid=uuid)

    if not record:
        return tool_error(f"No stored response found for UUID: {uuid}", uuid=uuid)

    return tool_success(
        {
            "tool_name": record["tool_name"],
            "response_text": record["response_text"],
            "metadata": record["metadata"],
            "timestamp": record["timestamp"],
        },
        uuid=uuid,
    )


def get_tool() -> list[dict]:
    return [
        {
            "type": "function",
            "safe": True,
            "categories": ["memory_read"],
            "function": {
                "name": "retrieve_tool_response",
                "description": (
                    "Retrieve a stored tool response by its UUID. "
                    "Use this to expand [TOOL_RESPONSE:<uuid>] placeholders "
                    "that appear in conversation history. Returns the original "
                    "tool output: tool_name, response_text, metadata, and timestamp."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uuid": {
                            "type": "string",
                            "description": "The UUID from a [TOOL_RESPONSE:<uuid>] placeholder.",
                        }
                    },
                    "required": ["uuid"],
                },
            },
            "func": retrieve_tool_response,
        }
    ]
