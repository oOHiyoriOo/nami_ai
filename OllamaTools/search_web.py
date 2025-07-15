import logging, json, asyncio, aiohttp

from typing import List, Dict, Optional, Any
from lib.global_registry import g_data

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

REQUEST_TIMEOUT = 10

def parse_brave_result(result_item: Dict[str, Any]) -> Optional[Dict[str, Optional[str]]]:
    """Extracts relevant fields from a Brave Search API result item."""
    try:
        title = result_item.get('title')
        url = result_item.get('url')
        description = result_item.get('description')
        if url:
            return {
                "title": str(title) if title else None,
                "url": str(url),
                "description": str(description) if description else None,
            }
        else:
            return None
    except Exception as e:
        logging.warning(f"Could not parse result item: {result_item}. Error: {e}")
        return None

async def search_web(
    client,
    source_user: str,
    query: str,
    pageno: int = 1,
    language: str = 'en',
    time_range: Optional[str] = None,
    safesearch: int = 1,
    categories: Optional[str] = 'general',
    max_results: Optional[int] = 10
) -> List[Dict[str, Optional[str]]]:
    """
    Rate limits:
    - 1 request per second (enforced below)
    - 2000 requests per month (not enforced in code, monitor usage externally)
    """
    await asyncio.sleep(1)

    params = {
        "q": query,
        "count": max_results or 10,
        "offset": (int(pageno) - 1) * (max_results or 10),
        "safesearch": "strict" if safesearch == 2 else "moderate" if safesearch == 1 else "off",
        "lang": language
    }
    headers = {
        "Accept": "application/json",
        "Accept-Language": language,
        "X-Subscription-Token": g_data.get("cfg").data['bot']['brave_search_token'],
        "User-Agent": "Mozilla/5.0"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(BRAVE_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT) as response:
                response.raise_for_status()
                data = await response.json()
                web_results = data.get("web", {}).get("results", [])
                results_list = []
                for item in web_results:
                    parsed = parse_brave_result(item)
                    if parsed:
                        results_list.append(parsed)
                        if max_results is not None and len(results_list) >= max_results:
                            break
                if results_list:
                    logging.info(f"Successfully retrieved {len(results_list)} results from Brave Search")
                    return results_list
                else:
                    logging.warning("No parseable results found from Brave Search.")
                    return [{"error": "No parseable results found", "title": None, "url": None, "description": None}]

    except Exception as e:
        logging.error(f"Unexpected error querying Brave Search: {e}")
        logging.exception(e)
        return json.dumps([{"error": f"Unexpected error: {e}", "title": None, "url": None, "description": None}])

def get_tool():
    """
    Returns the tool schema for integration (e.g., with Ollama).
    Uses the new search_web function.
    """
    return {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Perform a web search using Brave Search API and retrieve structured results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string."
                    },
                    "pageno": {
                        "type": "integer",
                        "description": "Page number of results (default 1).",
                        "default": 1
                    },
                    "language": {
                        "type": "string",
                        "description": "Language code for results (e.g., 'en', 'de', default 'en').",
                        "default": "en"
                    },
                    "time_range": {
                        "type": "string",
                        "description": "Filter results by time range ('day', 'month', 'year'). Can be null.",
                        "enum": ["day", "week", "month", "year", None],
                        "nullable": True,
                        "default": None
                    },
                    "safesearch": {
                        "type": "integer",
                        "description": "Safe search level (0: None, 1: Moderate, 2: Strict, default 1).",
                        "enum": ["0", "1", "2"],
                        "default": 1
                    },
                    "categories": {
                        "type": "string",
                        "description": "Comma-separated categories (e.g., 'general', 'news', default 'general').",
                        "default": "general"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Stop parsing after approximately this many results are found (default 10). Set to null for no limit.",
                        "nullable": True,
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        }
    }
