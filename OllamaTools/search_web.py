import logging
import discord
import googlesearch

async def search_web(client: discord.Client, source_id: str, query: str):
    try:
        # Perform the search
        search_results = googlesearch.search(query, num_results=10, safe=None, region="de", advanced=True)
        # Returns a list of SearchResult
        # Properties:
        # - title
        # - url
        # - description

        # Format the results
        results = []
        for result in search_results:
            result : googlesearch.SearchResult = result
            results.append(f"**{result.title}**\n{result.url}\n{result.description}")
            
        # Return the formatted results
        return "\n\n".join(results)

    except Exception as e:
        logging.error(f"Error querying user: {e}")
        logging.exception(e)
        return f"Error: {str(e)}"

def get_tool():
    return {
      "type": "function",
      "function": {
        "name": "search_web",
        "description": "Search the Web for information based on a query.",
        "parameters": {
          "type": "object",
          "properties": {
            "query": {
              "type": "string",
              "description": "Your search string."
            }
          }
        }
      }
    }