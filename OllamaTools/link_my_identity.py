"""
link_my_identity.py — Cross-platform identity linking tool.

Allows Nami to link a user's identity across platforms so their memory
graph is shared.  When someone says "I'm also X on Y platform", Nami
creates a :SAME_PERSON_AS relationship between the two Person nodes.
"""

import logging

from lib.global_registry import g_data
from lib.services.ai_pipeline import pipeline_ctx
from OllamaTools import tool_error, tool_success


async def link_my_identity(
    client=None,
    source_user=None,
    other_platform: str = "",
    other_id: str = "",
) -> str:
    """
    Link the current user's identity to their account on another platform.

    Use when someone says "I'm also X on Y platform" or "connect my
    Discord and WhatsApp accounts".

    Args:
        other_platform: Platform name (e.g. 'whatsapp', 'discord').
        other_id: Their ID or username on that platform.
    """
    try:
        db = g_data.get("memory_db")
        if not db:
            return tool_error("Memory database not available")

        # Resolve the current user's scoped ID from the pipeline context (e.g. "discord:123")
        ctx = pipeline_ctx.get()
        current_user_id = ctx.get("user_id", "")
        # Fallback to source_user for legacy/test paths
        if not current_user_id and source_user and hasattr(source_user, "id"):
            current_user_id = source_user.id
        if not current_user_id:
            return tool_error(
                "Cannot determine your current identity — no platform context available."
            )

        if not other_platform or not other_id:
            return tool_error(
                "Both other_platform and other_id are required. "
                "Example: other_platform='whatsapp', other_id='+4917612345'."
            )

        other_user_id = f"{other_platform.strip().lower()}:{other_id.strip()}"
        if other_user_id == current_user_id:
            return tool_error(
                f"Cannot link to yourself ({other_user_id} == {current_user_id})."
            )

        await db.link_person_identities(
            current_user_id, other_user_id, linked_by="user_request"
        )

        return tool_success({
            "linked": [current_user_id, other_user_id],
            "message": f"Identities linked: {current_user_id} ↔ {other_user_id}. "
                       f"Memories are now shared across platforms.",
        })

    except Exception as e:
        logging.error(f"Error in link_my_identity: {e}", exc_info=True)
        return tool_error(str(e))


def get_tool() -> list[dict]:
    """Return the link_my_identity tool schema."""
    return [{
        "type": "function",
        "safe": False,
        "categories": ["memory_write"],
        "function": {
            "name": "link_my_identity",
            "description": (
                "Link your current identity to your account on another platform. "
                "Use when someone says 'I'm also X on Y platform' — this connects "
                "their Person nodes so memories are shared across platforms."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "other_platform": {
                        "type": "string",
                        "description": "Platform name (e.g. 'whatsapp', 'discord').",
                    },
                    "other_id": {
                        "type": "string",
                        "description": "Their ID or username on that platform.",
                    },
                },
                "required": ["other_platform", "other_id"],
            },
        },
        "func": link_my_identity,
    }]
