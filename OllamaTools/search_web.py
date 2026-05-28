"""
search_web.py — Web search using DuckDuckGo.

Performs a text search and returns titles, URLs, and snippets for the top
results. No API key required. Use mcp_playwright_browser_navigate +
mcp_playwright_browser_snapshot to read the full content of a URL.

Referenced in the system prompt as the primary external information tool.
"""

import logging

from OllamaTools import tool_error, tool_success


async def search_web(query: str, max_results: int = 5) -> str:
    """
    Search the web using DuckDuckGo and return a list of results.

    Args:
        query:       Natural language or keyword search query.
        max_results: Max results to return (default 5, capped at 10).

    Returns:
        JSON list of {title, url, snippet} dicts.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return tool_error(
            "ddgs package not installed. Run: pip install ddgs"
        )

    max_results = max(1, min(max_results, 10))

    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        logging.info(f"[search_web] query={query!r} → {len(results)} results")
        return tool_success(results, query=query)
    except Exception as e:
        logging.error(f"[search_web] Error: {e}", exc_info=True)
        return tool_error(str(e), query=query)


def get_tool() -> list[dict]:
    return [{
        "type": "function",
        "safe": True,
        "categories": ["web"],
        "function": {
            "name": "search_web",
            "description": (
                "Search the web using DuckDuckGo. Returns titles, URLs, and snippets "
                "for the top results. Use mcp_playwright_browser_navigate + "
                "mcp_playwright_browser_snapshot to read the full content of a specific URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5, max 10).",
                    },
                },
                "required": ["query"],
            },
        },
        "func": search_web,
    }]
