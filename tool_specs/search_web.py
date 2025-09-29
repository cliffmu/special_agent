"""Tool for searching the web using Google Custom Search API."""

from __future__ import annotations

import logging
from typing import Any, Dict
import aiohttp
import json

import voluptuous as vol

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

# Global variables to store API credentials (set by agent when loading)
_GOOGLE_API_KEY = None
_GOOGLE_CX = None

PARAMS = vol.Schema(
    {
        vol.Required("query"): str,
        vol.Optional("max_results", default=3): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10)
        ),
    }
)


async def search_web(
    query: str,
    max_results: int = 3,
    hass: Any | None = None,
) -> Dict[str, Any]:
    """
    Search the web using Google Custom Search API.
    
    Returns structured search results that the agent can interpret.
    """
    log.debug(f"Google search: {query}")
    
    # Check if API credentials are available
    if not _GOOGLE_API_KEY or not _GOOGLE_CX:
        log.error("Google API credentials not configured")
        return {
            "query": query,
            "error": "Search not configured",
            "message": "Web search is not available. Google API credentials have not been configured."
        }
    
    try:
        async with aiohttp.ClientSession() as session:
            # Google Custom Search API endpoint
            url = "https://www.googleapis.com/customsearch/v1"
            params = {
                "key": _GOOGLE_API_KEY,
                "cx": _GOOGLE_CX,
                "q": query,
                "num": max_results,
            }
            
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Build structured response
                    result = {
                        "query": query,
                        "results": [],
                        "total_results": data.get("searchInformation", {}).get("totalResults", "0")
                    }
                    
                    # Extract search results
                    items = data.get("items", [])
                    for item in items:
                        result["results"].append({
                            "title": item.get("title", ""),
                            "snippet": item.get("snippet", ""),
                            "link": item.get("link", ""),
                            "source": item.get("displayLink", "")
                        })
                    
                    # Check for answer box or featured snippets
                    if "answerBox" in data:
                        answer_box = data["answerBox"]
                        if answer_box.get("answer"):
                            result["instant_answer"] = answer_box["answer"]
                        elif answer_box.get("snippet"):
                            result["featured_snippet"] = answer_box["snippet"]
                    
                    # Check for knowledge graph
                    if "knowledge_graph" in data:
                        kg = data["knowledge_graph"]
                        if kg.get("description"):
                            result["summary"] = kg["description"]
                    
                    return result
                    
                elif resp.status == 403:
                    error_data = await resp.json()
                    error_msg = error_data.get("error", {}).get("message", "API key invalid or quota exceeded")
                    log.error(f"Google API error: {error_msg}")
                    return {
                        "query": query,
                        "error": "API limit or authentication issue",
                        "message": f"Search failed: {error_msg}"
                    }
                else:
                    log.error(f"Google API returned status {resp.status}")
                    return {
                        "query": query,
                        "error": f"HTTP {resp.status}",
                        "message": "Search service returned an error. Please try again later."
                    }
                    
    except Exception as e:
        log.error(f"Google search exception: {e}")
        return {
            "query": query,
            "error": str(e),
            "message": "An error occurred while searching. Please try again."
        }


def set_credentials(api_key: str, cx: str) -> None:
    """Set the Google API credentials for this module."""
    global _GOOGLE_API_KEY, _GOOGLE_CX
    _GOOGLE_API_KEY = api_key
    _GOOGLE_CX = cx
    log.debug("Google search credentials configured")


# Tool specification
SPEC = ToolSpec(
    name="search_web",
    description=(
        "Search the web using Google for current information when your knowledge is insufficient "
        "or when the user asks about recent events, news, scores, or data after October 2024. "
        "Returns structured search results including titles, snippets, and links that you should interpret and summarize."
    ),
    parameters=PARAMS,
    returns="dict with search results including title, snippet, link for each result, plus total_results count",
    func=search_web,
)
