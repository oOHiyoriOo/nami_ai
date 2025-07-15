# Import necessary libraries
import logging
import asyncio
from typing import Dict, Optional
import aiohttp
import trafilatura

# --- Configuration ---
REQUEST_TIMEOUT = 20
HEADERS = { # Use a common user-agent
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'de-DE,de;q=0.5',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'DNT': '1'
}

# --- Main Tool Function ---
async def visit_web_page(client, source_user, url: str) -> Dict[str, Optional[str]]:
    """
    Fetches the content of a given URL, extracts the main text content using trafilatura,
    and returns the cleaned text.

    :param url: The URL of the web page to visit.
    :return: A dictionary containing the 'cleaned_text' on success,
             or an 'error' message on failure. Both include the original 'url'.
    """
    if not aiohttp or not trafilatura:
         error_msg = "Required library not installed: "
         if not aiohttp: error_msg += "aiohttp "
         if not trafilatura: error_msg += "trafilatura"
         logging.error(error_msg)
         return {"url": url, "cleaned_text": None, "error": error_msg}

    logging.info(f"Attempting to visit and extract content from: {url}")

    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=REQUEST_TIMEOUT, ssl=False) as response: # Added ssl=False for broader compatibility, consider security implications
                # Check if the request was successful
                if response.status != 200:
                     logging.warning(f"Failed to fetch URL {url}. Status: {response.status}")
                     return {"url": url, "cleaned_text": None, "error": f"HTTP status {response.status}"}

                # Read the HTML content
                # Check content type - proceed only if likely HTML
                content_type = response.headers.get('Content-Type', '').lower()
                if 'html' not in content_type:
                    logging.warning(f"URL {url} returned non-HTML content type: {content_type}")
                    return {"url": url, "cleaned_text": None, "error": f"Non-HTML content type: {content_type}"}
                    
                html_content = await response.text()

                if not html_content:
                    logging.warning(f"Fetched empty content from {url}")
                    return {"url": url, "cleaned_text": None, "error": "Fetched empty content"}

    except aiohttp.ClientError as e:
        logging.warning(f"Network/Client error fetching {url}: {e}")
        return {"url": url, "cleaned_text": None, "error": f"Network error: {e}"}
    except asyncio.TimeoutError:
        logging.warning(f"Timeout fetching {url}")
        return {"url": url, "cleaned_text": None, "error": "Request timed out"}
    except Exception as e:
        # Catch potential errors like invalid URL format before request
        logging.error(f"Error during fetch setup or request for {url}: {e}")
        return {"url": url, "cleaned_text": None, "error": f"Fetch error: {e}"}

    # --- Extract Content using Trafilatura ---
    try:
        # Use trafilatura.extract - include_tables=False can sometimes simplify output further
        # favor_recall=True might get more text but potentially more noise
        # include_comments=False is usually desired for summarization
        extracted_text = trafilatura.extract(
            html_content,
            include_comments=False,
            include_tables=True, # Set to False if tables often contain boilerplate
            favor_precision=True # Prioritize cleaner output over getting every last bit
        )

        if extracted_text:
            logging.info(f"Successfully extracted content from {url}")
            return {"url": url, "cleaned_text": extracted_text, "error": None}
        else:
            logging.warning(f"Trafilatura could not extract main content from {url}")
            # Optionally, return the raw text as fallback? Or just indicate failure.
            # fallback_text = trafilatura.extract(html_content, no_fallback=True) # Try without fallbacks?
            # Maybe try BeautifulSoup basic extraction as a final fallback?
            # For now, just return error if primary extraction fails.
            return {"url": url, "cleaned_text": None, "error": "Could not extract main content"}

    except Exception as e:
        logging.error(f"Error during Trafilatura extraction for {url}: {e}")
        logging.exception(e) # Log full traceback
        return {"url": url, "cleaned_text": None, "error": f"Content extraction error: {e}"}

def get_tool():
    """
    Returns the tool schema for the visit_web_page function.
    """
    return {
        "type": "function",
        "function": {
            "name": "visit_web_page",
            "description": "Fetches the HTML content of a given URL, extracts the main textual content using Trafilatura (removing ads/boilerplate), and returns the cleaned text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The fully qualified URL of the web page to visit and extract content from."
                    }
                },
                "required": ["url"]
            },
            "returns": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The original URL visited."
                    },
                    "cleaned_text": {
                        "type": "string",
                        "description": "The extracted main text content of the page, or null if extraction failed.",
                        "nullable": True
                    },
                    "error": {
                        "type": "string",
                        "description": "An error message if fetching or extraction failed, otherwise null.",
                        "nullable": True
                    }
                }
            }
        }
    }