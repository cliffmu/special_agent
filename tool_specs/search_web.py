"""Tool for web search using OpenAI's built-in web search capability."""

from __future__ import annotations

import logging
from typing import Any, Dict

import voluptuous as vol

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

PARAMS = vol.Schema(
    {
        vol.Required("query"): str,
        vol.Optional("reasoning_effort", default="low"): vol.In([
            "minimal", "low", "medium", "high"
        ]),
    }
)


async def search_web(
    query: str,
    reasoning_effort: str = "low",
    hass: Any | None = None,
) -> Dict[str, Any]:
    """
    Search the web using OpenAI's built-in web search.
    
    This leverages OpenAI's web_search tool which includes:
    - Real-time sports scores (oai-sports feed)
    - Current weather (oai-weather feed)
    - Financial data (oai-finance feed)
    - General web results with citations
    
    Returns structured results with answer and sources.
    """
    log.debug(f"OpenAI web search: {query} (effort={reasoning_effort})")
    
    try:
        from ..utils.openai_client import get_async_client
        client = await get_async_client(hass)
        
        # Get user location from Home Assistant for better local results
        user_location = None
        if hass:
            try:
                # Get timezone and location from HA config
                if hasattr(hass.config, 'time_zone'):
                    timezone = hass.config.time_zone
                    user_location = {
                        "type": "approximate",
                        "timezone": timezone
                    }
                    # Try to add location name if available
                    if hasattr(hass.config, 'location_name') and hass.config.location_name:
                        user_location["city"] = hass.config.location_name
            except Exception as e:
                log.debug(f"Could not get user location: {e}")
        
        # Build tools config
        web_search_tool = {"type": "web_search"}
        if user_location:
            web_search_tool["user_location"] = user_location
        
        # Call OpenAI Responses API
        response = await client.responses.create(
            model="gpt-5",
            reasoning={"effort": reasoning_effort},
            tools=[web_search_tool],
            tool_choice="auto",
            include=["web_search_call.action.sources"],
            input=query,
        )
        
        # Extract response
        result = {
            "query": query,
            "answer": response.output_text,
            "sources": [],
        }
        
        # Extract sources from web_search_call items
        for item in response.output:
            if item.get("type") == "web_search_call":
                action = item.get("action", {})
                sources_list = action.get("sources", [])
                if sources_list:
                    result["sources"] = sources_list
        
        log.debug(f"OpenAI search returned {len(result.get('sources', []))} sources")
        return result
        
    except Exception as e:
        log.error(f"OpenAI web search failed: {e}")
        return {
            "query": query,
            "error": str(e),
            "message": f"Web search failed: {str(e)}"
        }


# Tool specification
SPEC = ToolSpec(
    name="search_web",
    description=(
        "Search the web using OpenAI for current information when your knowledge is insufficient "
        "or when the user asks about recent events, news, scores, weather, or data after October 2024. "
        "This tool has access to real-time sports scores (oai-sports), weather (oai-weather), "
        "and financial data (oai-finance), plus general web results with citations. "
        "Returns answer text already interpreted by GPT-5, plus source URLs."
    ),
    parameters=PARAMS,
    returns="dict with 'answer' (interpreted text) and 'sources' (list of URLs)",
    func=search_web,
)