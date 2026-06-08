"""
create_person.py — Create or update a PERSON node in Nami's memory graph.

Allows Nami to proactively record knowledge about people she encounters,
even those who aren't direct message authors.
"""

import logging

from lib.global_registry import g_data
from OllamaTools import tool_error, tool_success
from lib.utils import slugify


async def create_person(name: str, description: str = "", relationship: str = "") -> str:
    """
    Create or update a PERSON node in Nami's memory graph.

    Args:
        name: The person's name or handle
        description: Brief description of who this person is
        relationship: Nami's relationship to this person (e.g. "alice's colleague")
    """
    try:
        db = g_data.get("memory_db")
        if not db:
            return tool_error("Memory database not available")

        user_id = slugify(name)
        await db.add_person(
            user_id=user_id,
            name=name,
            description=description,
            metadata={"relationship": relationship} if relationship else {}
        )
        return tool_success({"person_id": user_id, "name": name, "action": "upserted"})

    except Exception as e:
        logging.error(f"Error in create_person: {e}")
        return tool_error(str(e))


def get_tool() -> list[dict]:
    """Return the create_person tool schema."""
    return [{
        "type": "function",
        "safe": False,
        "categories": ["memory_write"],
        "function": {
            "name": "create_person",
            "description": "Create or update a PERSON node in Nami's memory graph. Use this to remember someone's name, who they are, and your relationship to them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The person's name or handle"
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of who this person is"
                    },
                    "relationship": {
                        "type": "string",
                        "description": "Nami's relationship to this person (e.g. \"alice's colleague\")"
                    }
                },
                "required": ["name"]
            }
        },
        "func": create_person,
    }]
