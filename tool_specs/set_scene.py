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
        },
        "client_config": {
            "type": "object",
            "description": "Optional client config for tools (e.g., client_ip, plex_client_entity). Used for play_plex_media and similar."
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
    client_config: Dict | None = None,
    hass: Any | None = None
) -> Dict[str, str]:
    """
    Write back the outcome of an executed scene routine for learning.
    
    This updates the memory store and rebuilds the scene index incrementally.
    Performance is tracked to determine if async write-back is needed.
    
    IMPORTANT: This should only be called AFTER run_sequence has been tested.
    Steps with malformed formats or missing entity_ids will be rejected.
    """
    log.debug("set_scene: intent=%s, area=%s, outcome=%s, steps=%d", 
              intent, area, outcome, len(steps))
    
    # Validate we have actual steps
    if not steps or len(steps) == 0:
        log.warning("set_scene called with no steps, rejecting")
        return {
            "status": "error",
            "error": "Cannot save scene with no steps",
            "message": "Provide the actual steps that were executed"
        }
    
    try:
        # Track performance of this operation
        async with performance.track_operation(
            "tool_set_scene",
            metadata={"intent": intent, "outcome": outcome, "steps": len(steps)}
        ):
            from ..utils.vector_index import async_upsert_scene
            from ..utils.scene_memory_store import normalize_and_validate_steps
            import time
            
            # Normalize and validate steps using utility
            normalized_steps, validation_errors = normalize_and_validate_steps(steps)
            
            # If too many validation errors, reject the save
            if len(validation_errors) >= len(steps) / 2:
                error_msg = "Too many malformed steps:\n" + "\n".join(validation_errors[:5])
                log.error("Rejecting set_scene: %s", error_msg)
                return {
                    "status": "error",
                    "error": error_msg,
                    "message": "Scene not saved - use entity_ids from search_devices, not friendly names"
                }
            
            # Log warnings for skipped steps
            if validation_errors:
                for err in validation_errors:
                    log.warning("Scene validation: %s", err)
            
            # Build entry
            entry_id = f"{intent}_{area or 'global'}_{int(time.time())}"
            confidence = 0.8 if outcome == "success" else 0.3 if outcome == "fail" else 0.6
            
            step_types = [s.get("type", "unknown") for s in normalized_steps]
            summary = f"{len(normalized_steps)} steps: {', '.join(step_types[:3])}"
            if len(step_types) > 3:
                summary += f", +{len(step_types) - 3} more"
            
            entry = {
                "id": entry_id,
                "intent": intent,
                "area_hint": area,
                "summary": summary,
                "steps": normalized_steps,
                "strategy": notes or f"Learned from {outcome} execution",
                "confidence": confidence,
                "updated_at": time.time(),
                "client_config": client_config or {},  # Store client IPs, entity overrides, etc.
            }
            
            # Update store and rebuild index
            await async_upsert_scene(entry, hass=hass)
            
            log.info("Scene saved: intent=%s, outcome=%s, steps=%d", 
                     intent, outcome, len(normalized_steps))
            
            return {
                "status": "ok", 
                "message": f"Scene '{intent}' saved with confidence {confidence:.2f}",
                "entry_id": entry_id
            }
        
    except Exception as err:
        log.error("Error writing scene result: %s", err, exc_info=True)
        return {"status": "error", "error": str(err)}


SPEC = ToolSpec(
    name="set_scene",
    description=(
        "Save scene after successful execution. ONLY call if run_sequence result='completed' (all steps ok). "
        "Steps need entity_ids from search_devices (not friendly names). Validates & rejects malformed. "
        "client_config (optional): Dict for tool parameters not in steps - e.g., {\"client_ip\": \"192.168.86.208\"} for Plex. "
        "Stored with scene, retrieved by get_scene for reuse. "
        "Outcome: 'success'/'corrected'/'fail'. If error returned: fix entity_ids and retry."
    ),
    parameters=PARAMS,
    returns="dict with status",
    func=set_scene,
    can_run_parallel=True,
)

