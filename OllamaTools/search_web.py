import logging, json
from googlesearch import search
from typing import List, Dict

async def search_web(client, source_id: str, query: str) -> List[Dict[str, str]]:
    """
    Perform a web search and return the results in a structured format.

    :param client: The Ollama AI client (not used here, included for consistency).
    :param source_id: Identifier for the request source (not used here, included for consistency).
    :param query: The search query string.
    :return: A list of dictionaries containing search result details.
    """
    try:
        # Perform the search
        search_results = search(query, num_results=5, safe="off", region="de", advanced=True)

        # Structure the results
        results = [
            {
                "title": str(result.title),
                "url": str(result.url),
                "description": str(result.description),
            }
            for result in search_results
        ]

        # Return the structured results
        return json.dumps(results)

    except Exception as e:
        logging.error(f"Error querying web: {e}")
        logging.exception(e)
        return [{"error": str(e)}]

def get_tool():
    """
    Returns the tool schema for Ollama AI integration.
    """
    return {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Perform a web search and retrieve structured results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string."
                    }
                },
                "required": ["query"]
            }
        }
    }
