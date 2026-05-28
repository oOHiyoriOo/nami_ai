import logging
import re

from lib.global_registry import g_data
from OllamaTools import tool_error, tool_success


async def _resolve_person(db, person: str) -> str | None:
    """Resolve a person string (name or scoped ID) to a user_id for filtering."""
    # Direct scoped ID match (contains ':')
    if ":" in person:
        return person
    # Name match via Neo4j
    try:
        driver = db.get_driver()
        async with driver.session() as session:
            result = await session.run(
                "MATCH (p:Person) WHERE p.name =~ $pattern RETURN p.id AS user_id LIMIT 1",
                {"pattern": f"(?i).*{re.escape(person)}.*"},
            )
            records = [record async for record in result]
            if records:
                return records[0]["user_id"]
    except Exception as e:
        logging.warning(f"Person resolution failed for '{person}': {e}")
    return None


async def search_memory(query: str, person: str = "") -> str:
    """
    Search Nami's memory graph.

    Args:
        query: What to search for
        person: Optional. Filter results to memories from/about a specific person
                (use their name, handle, or scoped ID like 'discord:123')
    """
    try:
        db = g_data.get("memory_db")
        if not db:
            return tool_error("Memory database not available", query=query)

        filter_user_id = await _resolve_person(db, person) if person else None
        results = await db.search(query, top_k=5, filter_user_id=filter_user_id)

        # Structure the results
        structured_results = [
            {"memory": str(result[0]), "similarity": str(result[1])} 
            for result in results
        ]
        
        return tool_success(structured_results, query=query)

    except Exception as e:
        logging.error(f"Error querying memory: {e}")
        return tool_error(str(e), query=query)

def get_tool() -> list[dict]:
    """
    Returns the memory search tool schema for Ollama AI integration.
    """
    return [{
        "type": "function",
        "safe": True,
        "categories": ["memory_read"],
        "function": {
            "name": "search_memory",
            "description": "Search the memory database for relevant past interactions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string, typically a user message."
                    },
                    "person": {
                        "type": "string",
                        "description": "Optional. Filter results to memories from/about a specific person (use their name, handle, or scoped ID like 'discord:123')."
                    }
                },
                "required": ["query"]
            }
        },
        "func": search_memory,
    }]

