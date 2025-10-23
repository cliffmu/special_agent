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
    Retrieve learned routines matching the intent.
    
    Returns:
        scenes: List of matching scenes with full metadata
        count: Number of scenes found
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
                "scenes": [],
                "count": 0,
                "message": f"No learned scenes found for '{intent}'"
            }
        
        # Format all results with full metadata
        scenes = []
        for result in results:
            scene = {
                "id": result.get("id"),
                "intent": result.get("intent"),
                "area": result.get("area_hint"),
                "commands_list": result.get("steps"),
                "confidence": result.get("confidence", 0.0),
                "search_score": result.get("search_score", 0.0),
                "summary": result.get("summary", ""),
                "strategy": result.get("strategy", ""),
                "client_config": result.get("client_config", {})
            }
            scenes.append(scene)
        
        log.info("Found %d scene(s): top intent=%s, confidence=%.2f, score=%.3f", 
                 len(results), results[0].get("intent"), 
                 results[0].get("confidence", 0), results[0].get("search_score", 0))
        
        return {
            "scenes": scenes,
            "count": len(scenes)
        }
        
    except Exception as err:
        log.error("Error retrieving scene: %s", err, exc_info=True)
        return {
            "scenes": [],
            "count": 0,
            "error": str(err)
        }


SPEC = ToolSpec(
    name="get_scene",
    description=(
        "Retrieve learned, deterministic routines (scenes) containing pre-tested step sequences.\n\n"
        "USE FOR:\n"
        "✓ Multi-step workflows - device power, app switching, media playback\n"
        "✓ Complex device setups - learned timing, IP addresses, entity mappings\n"
        "✓ Vibe-based requests - 'cozy', 'movie mode', saved preferences\n"
        "✗ Simple single-step actions - lights, switches (use direct control)\n\n"
        "SEARCH PATTERNS (can search multiple times in parallel for coverage):\n"
        "- Exact: intent='ACTION_ROOM', area='ROOM', k=1 (room-specific routine)\n"
        "- Template: intent='ACTION', k=3 (similar routines from other rooms to adapt)\n"
        "- Use both patterns simultaneously to maximize hit rate\n\n"
        "EXECUTE WORKFLOW:\n"
        "1. Get scenes → receive list of matching scenes with commands_list\n"
        "2. Pick best match (check intent, area, confidence, search_score)\n"
        "3. Bind variables: replace ${rating_key}, ${entity_id}, etc. with actual values\n"
        "4. Execute steps using sequential execution tool (pass commands_list + vars)\n"
        "✓ Compare multiple results to find best fit\n"
        "✓ Use scenes from other rooms as templates\n"
        "✓ Reuse structure even if content differs (e.g., same device setup, different media)\n"
        "✓ Scenes contain proven timing delays and entity targets\n\n"
        "Returns: scenes[] (list of matches, each with id, intent, area, commands_list, confidence, "
        "search_score, summary, strategy, client_config), count (number found)."
    ),
    parameters=PARAMS,
    returns="dict with scenes (list of scene objects), count",
    func=get_scene,
    can_run_parallel=True,
)

