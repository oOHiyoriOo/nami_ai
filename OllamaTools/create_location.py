"""
create_location.py — Create or update a LOCATION node in Nami's memory graph.

Allows Nami to record and remember places — physical or conceptual spaces
where events occur or where people gather.
"""

import logging

from lib.global_registry import g_data
from OllamaTools import tool_error, tool_success
from lib.services.memory_extractor import slugify


async def create_location(name: str, description: str = "") -> str:
    """
    Create or update a LOCATION node in Nami's memory graph.

    Args:
        name: Location name (e.g. "the office", "Zero Lab", "Berlin")
        description: What this place is
    """
    try:
        db = g_data.get("memory_db")
        if not db:
            return tool_error("Memory database not available")

        location_id = slugify(name)
        await db.add_location(
            location_id=location_id,
            name=name,
            description=description,
        )
        return tool_success({"location_id": location_id, "name": name, "action": "upserted"})

    except Exception as e:
        logging.error(f"Error in create_location: {e}")
        return tool_error(str(e))


def get_tool():
    """Return the create_location tool schema."""
    return {
        "type": "function",
        "safe": False,
        "categories": ["memory_write"],
        "function": {
            "name": "create_location",
            "description": "Create or update a LOCATION node in Nami's memory graph. Use this to remember places — offices, cities, online spaces, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Location name (e.g. \"the office\", \"Zero Lab\", \"Berlin\")"
                    },
                    "description": {
                        "type": "string",
                        "description": "What this place is"
                    }
                },
                "required": ["name"]
            }
        },
        "func": create_location,
    }
