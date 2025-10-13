"""Tool for retrieving learned scene routines from memory."""
from __future__ import annotations

import logging
from typing import Any, Dict

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

# OpenAI JSON schema format
PARAMS = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "description": "User intent or scene name (e.g., 'movie', 'cozy', 'good night')"
        },
        "area": {
            "type": "string",
            "description": "Optional area/room to filter by (e.g., 'living_room', 'bedroom')"
        },
        "k": {
            "type": "integer",
            "description": "Number of scenes to retrieve (default: 1)",
            "default": 1
        }
    },
    "required": ["intent"]
}


async def get_scene(
    intent: str,
    area: str | None = None,
    k: int = 1,
    hass: Any | None = None
) -> Dict[str, Any]:
    """
    Retrieve the best learned routine for a given intent.
    
    Returns:
        commands_list: Ordered steps for run_sequence (or null if not found)
        confidence: 0-1 score
        strategy_item: Optional strategy text for prompt injection
    """
    log.debug("get_scene: intent=%s, area=%s, k=%d", intent, area, k)
    
    try:
        from ..utils.scene_memory_index import async_search_scenes
        
        # Search for matching scenes
        results = await async_search_scenes(intent, area=area, k=k, hass=hass)
        
        if not results:
            log.debug("No scenes found for intent=%s, area=%s", intent, area)
            return {
                "commands_list": None,
                "confidence": 0.0,
                "strategy_item": None,
                "message": f"No learned scenes found for '{intent}'"
            }
        
        # Return top result
        top_result = results[0]
        log.info("Found scene: intent=%s, confidence=%.2f, score=%.3f", 
                 intent, top_result.get("confidence", 0), top_result.get("search_score", 0))
        
        # Format strategy item if available
        strategy_item = None
        if top_result.get("strategy"):
            strategy_item = {
                "title": top_result.get("intent", intent),
                "description": top_result.get("summary", ""),
                "content": top_result.get("strategy", "")
            }
        
        return {
            "commands_list": top_result.get("steps"),
            "confidence": top_result.get("confidence", 0.0),
            "strategy_item": strategy_item
        }
        
    except Exception as err:
        log.error("Error retrieving scene: %s", err, exc_info=True)
        return {
            "commands_list": None,
            "confidence": 0.0,
            "strategy_item": None,
            "error": str(err)
        }


SPEC = ToolSpec(
    name="get_scene",
    description=(
        "Retrieve learned scene routines from memory. "
        "Returns ordered steps, confidence score, and optional strategy text. "
        "Use for: 'movie time', 'cozy', 'good night', or any multi-device scene request. "
        "If confidence is high (>0.6), use the returned commands_list with run_sequence."
    ),
    parameters=PARAMS,
    returns="dict with commands_list, confidence, and strategy_item",
    func=get_scene,
    can_run_parallel=True,
)

