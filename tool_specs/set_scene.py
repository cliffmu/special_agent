"""Tool for writing back scene execution results for continual learning."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..agent_core import ToolSpec
from ..utils import logging as log
from ..utils import performance

_LOGGER = logging.getLogger(__package__)

# OpenAI JSON schema format
PARAMS = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "description": "Scene intent or name that was executed"
        },
        "area": {
            "type": "string",
            "description": "Optional area/room where scene was executed"
        },
        "steps": {
            "type": "array",
            "description": "List of steps that were executed",
            "items": {"type": "object"}
        },
        "outcome": {
            "type": "string",
            "description": "Execution outcome",
            "enum": ["success", "fail", "corrected"]
        },
        "notes": {
            "type": "string",
            "description": "Optional notes about the execution"
        }
    },
    "required": ["intent", "steps", "outcome"]
}


async def set_scene(
    intent: str,
    steps: List[Dict],
    outcome: str,
    area: str | None = None,
    notes: str | None = None,
    hass: Any | None = None
) -> Dict[str, str]:
    """
    Write back the outcome of an executed scene routine for learning.
    
    This updates the memory store and rebuilds the scene index incrementally.
    Performance is tracked to determine if async write-back is needed.
    """
    log.debug("set_scene: intent=%s, area=%s, outcome=%s, steps=%d", 
              intent, area, outcome, len(steps))
    
    try:
        # Track performance of this operation
        async with performance.track_operation(
            "tool_set_scene",
            metadata={"intent": intent, "outcome": outcome, "steps": len(steps)}
        ):
            # Import scene memory modules (will be created in Phase 4-5)
            try:
                from ..utils.scene_memory_store import upsert as store_upsert
                from ..utils.scene_memory_index import upsert_scene
                
                # Build memory entry
                import time
                entry = {
                    "id": f"{intent}_{area or 'global'}_{int(time.time())}",
                    "intent": intent,
                    "area_hint": area,
                    "steps": steps,
                    "outcome": outcome,
                    "notes": notes or "",
                    "updated_at": time.time(),
                }
                
                # Update store
                await store_upsert(entry, hass=hass)
                
                # Rebuild scene index (full rebuild for now)
                await upsert_scene(entry, hass=hass)
                
                log.info("Scene learning recorded: intent=%s, outcome=%s", intent, outcome)
                
                return {"status": "ok", "message": f"Scene '{intent}' learning recorded"}
                
            except ImportError:
                # Scene memory modules not yet implemented
                log.debug("Scene memory modules not available yet - write-back skipped")
                return {
                    "status": "ok", 
                    "message": "Scene memory backend not initialized (Phase 4-5 pending)"
                }
        
    except Exception as err:
        log.error("Error writing scene result: %s", err, exc_info=True)
        return {"status": "error", "error": str(err)}


SPEC = ToolSpec(
    name="set_scene",
    description=(
        "Record the outcome of a scene execution for continual learning. "
        "Call this after running a scene routine via run_sequence. "
        "Provide the intent, steps executed, and outcome (success/fail/corrected). "
        "This helps the system learn and improve scene routines over time."
    ),
    parameters=PARAMS,
    returns="dict with status",
    func=set_scene,
    can_run_parallel=True,  # Can track learning in background
)

