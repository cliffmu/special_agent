"""Tool for searching the web for general information."""

from __future__ import annotations

import logging
from typing import Any, Dict
import aiohttp
import json

import voluptuous as vol

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

PARAMS = vol.Schema(
    {
        vol.Required("query"): str,
        vol.Optional("max_results", default=3): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=5)
        ),
    }
)


async def search_web(
    query: str,
    max_results: int = 3,
    hass: Any | None = None,
) -> Dict[str, Any]:
    """
    Search the web for information using DuckDuckGo Instant Answer API.
    
    Returns a dict with search results that the agent can interpret.
    """
    log.debug(f"Web search: {query}")
    
    try:
        async with aiohttp.ClientSession() as session:
            # DuckDuckGo Instant Answer API (free, no key required)
            url = "https://api.duckduckgo.com/"
            params = {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
            }
            
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Build structured response
                    result = {
                        "query": query,
                        "results": []
                    }
                    
                    # Extract answer from various fields
                    if data.get("Answer"):
                        result["instant_answer"] = data["Answer"]
                    
                    if data.get("AbstractText"):
                        result["summary"] = data["AbstractText"]
                        if data.get("AbstractURL"):
                            result["source"] = data["AbstractURL"]
                    
                    if data.get("Definition"):
                        result["definition"] = data["Definition"]
                    
                    # Related topics for context
                    related = data.get("RelatedTopics", [])[:max_results]
                    for topic in related:
                        if isinstance(topic, dict) and topic.get("Text"):
                            result["results"].append({
                                "text": topic["Text"],
                                "url": topic.get("FirstURL", "")
                            })
                    
                    # If we have any meaningful data, return it
                    if any(result.get(k) for k in ["instant_answer", "summary", "definition", "results"]):
                        return result
                    
    except Exception as e:
        log.error(f"Web search failed: {e}")
    
    # Fallback response
    return {
        "query": query,
        "error": "Unable to retrieve search results",
        "fallback_message": f"I couldn't find current information about '{query}'. The search service may be temporarily unavailable."
    }


# Tool specification
SPEC = ToolSpec(
    name="search_web",
    description=(
        "Search the web for current information when your knowledge is insufficient "
        "or when the user asks about recent events, news, scores, or data after October 2024. "
        "Returns structured search results that you should interpret and summarize."
    ),
    parameters=PARAMS,
    returns="dict with search results including instant_answer, summary, or error message",
    func=search_web,
)
