import logging, json, traceback

from lib.global_registry import g_data
from typing import List, Dict
from googlesearch import search

async def search_memory(client, source_user, query: str) -> List[Dict[str, str]]:
    """
    Perform a memory search and return the results in a structured format.
    
    :param client: The Ollama AI client.
    :param source_user: Identifier for the request source.
    :param query: The search query string (typically a user message).
    :return: A list of dictionaries containing memory results.
    """
    try:
        results = g_data.get("memory_db").search(query, top_k=5)
        
        # Structure the results
        structured_results = [
            {"memory": str( result[0] ), "similarity": str( result[1] ) } for result in results
        ]
        
        return json.dumps( structured_results )

    except Exception as e:
        logging.error(f"Error querying memory: {e}")
        error_traceback = traceback.format_exc()
        return json.dumps({"error": str(e), "traceback": error_traceback})

def get_tool():
    """
    Returns the memory search tool schema for Ollama AI integration.
    """
    return {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search the memory database for relevant past interactions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string, typically a user message."
                    }
                },
                "required": ["query"]
            }
        }
    }

