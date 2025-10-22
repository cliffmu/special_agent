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
            "description": (
                "Scene intent to search for. Examples:\n"
                "- Specific: 'play_media_gym' (exact room)\n"
                "- Wildcard: 'play_media' (find similar TV setups in ANY room)\n"
                "- Generic: 'movie', 'cozy', 'good_night'\n"
                "For TV playback, use 'play_media_ROOM' pattern"
            )
        },
        "area": {
            "type": "string",
            "description": "Optional area/room to filter by. Omit to search all areas for similar scenes."
        },
        "k": {
            "type": "integer",
            "description": "Number of scenes to retrieve (default: 1, increase to find alternatives)",
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
        from ..utils.vector_index import async_search_scenes
        
        # Handle wildcard searches (e.g., "play_media" to find all media scenes)
        # Remove trailing _ or * for wildcard matching
        search_intent = intent.rstrip("_*")
        
        # Search for matching scenes
        results = await async_search_scenes(search_intent, area=area, k=k, hass=hass)
        
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
            "strategy_item": strategy_item,
            "client_config": top_result.get("client_config", {})  # Tool parameters (e.g., client_ip)
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
        "Retrieve learned scene routines for multi-step actions. **Call in PARALLEL with search tools in Call-1.**\n\n"
        "WHEN TO USE SCENES:\n"
        "✓ Multi-step workflows (turn on TV → open app → play media)\n"
        "✓ Ambiguous entities (which TV? which Plex client needs discovery)\n"
        "✓ Vibe-based requests ('cozy', 'movie night', 'focus mode')\n"
        "✗ Simple parallel actions ('turn on gym lights' - use control_device)\n"
        "✗ Single entity control ('pause TV' - use control_device)\n\n"
        "CALL-1 PATTERN (parallel):\n"
        "get_scene(intent='play_media_gym') + search_plex(...) + search_devices(...)\n"
        "- Get exact scene (intent='ACTION_ROOM', area=ROOM)\n"
        "- Get similar scenes if exact not found (intent='ACTION', k=3) from OTHER rooms\n"
        "- Do device/media searches simultaneously\n\n"
        "CALL-2: run_sequence with scene + bound variables.\n\n"
        "ADAPTATION: Similar scene = template. Adapt entity_ids, adjust steps for target room.\n"
        "Returns: commands_list, confidence, strategy_item, client_config (reuse IPs, settings)."
    ),
    parameters=PARAMS,
    returns="dict with commands_list, confidence, strategy_item, client_config",
    func=get_scene,
    can_run_parallel=True,
)

