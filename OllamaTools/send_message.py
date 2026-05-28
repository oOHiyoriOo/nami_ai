"""
send_message.py — Proactive outbound messaging tool.

Allows Nami to send a message to any connected adapter without
waiting for the user to write first. Delivery is routed through
the ``message.send`` event — AdapterManager handles actual dispatch.

Contact details (adapter names, conversation IDs, user IDs) should be
stored in memory so Nami can look them up by name:
  "Zero's Discord conversation: discord | 123456789012345"
  "Zero's WhatsApp chat: whatsapp | 4916095356029@c.us"
  "Zero's DM user ID: discord | user:987654321098"

Adapter and recipient formats:
  - adapter:    the adapter name, e.g. "discord", "whatsapp"
  - recipient:  conversation_id (e.g. "123456789012")
                or "user:<user_id>" to send a direct message (e.g. "user:987654321098")
"""

import logging

from lib.global_registry import g_data
from lib.services.event_bus import Event
from OllamaTools import tool_success, tool_error


async def send_message(adapter: str, recipient: str, message: str) -> str:
    """
    Send a proactive message to a person on any connected adapter.

    Args:
        adapter:   The adapter name to send through (e.g. "discord", "whatsapp").
        recipient: Conversation ID, or "user:<user_id>" for a direct message.
        message:   Text content to send.
    """
    event_bus = g_data.get("event_bus")
    if not event_bus:
        return tool_error("No event_bus available — application not fully initialised.")

    adapter = adapter.lower().strip()
    recipient = recipient.strip()

    await event_bus.publish(Event(
        type="message.send",
        data={"adapter": adapter, "recipient": recipient, "content": message},
    ))

    target_type = "dm" if recipient.startswith("user:") else "conversation"
    logging.info("[send_message] %s → %s/%s: %s", target_type, adapter, recipient, message[:60])
    return tool_success({"adapter": adapter, "to": recipient, "type": target_type})


def get_tool() -> list[dict]:
    """Return the send_message tool schema."""
    return [{
        "type": "function",
        "safe": False,
        "categories": ["communication"],
        "function": {
            "name": "send_message",
            "description": (
                "Proactively send a message to someone on any connected adapter (Discord, WhatsApp, etc.) "
                "without waiting for them to write first. Use this to notify people, "
                "share updates, or reach out when something important happened. "
                "Contact details (adapter names, conversation IDs, user IDs) are stored in memory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "adapter": {
                        "type": "string",
                        "description": (
                            "The adapter to send through, e.g. 'discord' or 'whatsapp'. "
                            "Must be a currently connected adapter."
                        ),
                    },
                    "recipient": {
                        "type": "string",
                        "description": (
                            "Conversation ID (e.g. '123456789012') to send to a channel or chat, "
                            "or 'user:<user_id>' (e.g. 'user:987654321098') to send a direct message."
                        ),
                    },
                    "message": {
                        "type": "string",
                        "description": "The text message to send.",
                    },
                },
                "required": ["adapter", "recipient", "message"],
            },
        },
        "func": send_message,
    }]

