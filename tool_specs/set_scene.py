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
    "required": ["intent", "outcome"]
}


async def set_scene(
    intent: str,
    outcome: str,
    steps: List[Dict] | None = None,
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
    
    If steps are not provided, they will be retrieved from the existing scene (for updates).
    """
    log.debug("set_scene: intent=%s, area=%s, outcome=%s, steps=%s", 
              intent, area, outcome, f"{len(steps)} provided" if steps else "fetching from memory")
    entry_id = f"{intent}_{area or 'global'}"
    
    # If no steps provided, try to fetch from existing scene
    if not steps or len(steps) == 0:
        if outcome == "success":
            # Try to fetch existing scene
            try:
                from ..utils.scene_memory_store import async_get
                existing = await async_get(entry_id, hass)
                
                if existing and existing.get("steps"):
                    steps = existing.get("steps")
                    log.info("set_scene: Retrieved %d steps from existing scene %s", len(steps), entry_id)
                else:
                    log.warning("set_scene: No steps provided and no existing scene found for %s", entry_id)
                    return {
                        "status": "error",
                        "error": "No steps provided and no existing scene found",
                        "message": "For new scenes, you must provide steps. For updates, ensure scene exists."
                    }
            except Exception as e:
                log.error("set_scene: Failed to fetch existing scene: %s", e)
                return {
                    "status": "error",
                    "error": f"Cannot retrieve existing scene: {str(e)}",
                    "message": "Provide steps parameter to save scene"
                }
        else:
            # For failures, steps are required to understand what went wrong
            log.warning("set_scene called with outcome=%s but no steps", outcome)
            return {
                "status": "error",
                "error": "Steps required for failure outcomes",
                "message": "Provide the steps that failed so we can learn from them"
            }
    
    try:
        # Track performance of this operation
        async with performance.track_operation(
            "tool_set_scene",
            metadata={"intent": intent, "outcome": outcome, "steps": len(steps)}
        ):
            from ..utils.vector_index import async_upsert_scene
            from ..utils.scene_memory_store import (
                normalize_and_validate_steps, 
                should_update_scene,
                async_get,
            )
            import time
            
            # Normalize and validate steps
            normalized_steps, validation_errors = normalize_and_validate_steps(steps)
            
            # Reject if too many errors
            if len(validation_errors) >= len(steps) / 2:
                error_msg = "Too many malformed steps:\n" + "\n".join(validation_errors[:5])
                log.error("Rejecting set_scene: %s", error_msg)
                return {
                    "status": "error",
                    "error": error_msg,
                    "message": "Scene not saved - use entity_ids from search_devices"
                }
            
            # Log warnings for skipped steps
            for err in validation_errors:
                log.warning("Scene validation: %s", err)
            
            # Stable ID for updates
            confidence = 0.8 if outcome == "success" else 0.3 if outcome == "fail" else 0.6
            
            # Check if update needed
            existing = await async_get(entry_id, hass)
            should_update, update_reason = should_update_scene(existing, normalized_steps, outcome)
            
            if not should_update:
                log.debug("Scene '%s' %s, skipping update", entry_id, update_reason)
                return {
                    "status": "skipped",
                    "message": f"Scene '{intent}' unchanged, no update needed",
                    "entry_id": entry_id
                }
            
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
            
            log.info("Scene %s: intent=%s, reason=%s, steps=%d", 
                     update_reason, intent, update_reason, len(normalized_steps))
            
            return {
                "status": "ok", 
                "message": f"Scene '{intent}' {update_reason} with confidence {confidence:.2f}",
                "entry_id": entry_id
            }
        
    except Exception as err:
        log.error("Error writing scene result: %s", err, exc_info=True)
        return {"status": "error", "error": str(err)}


SPEC = ToolSpec(
    name="set_scene",
    description=(
        "Save scene after successful multi-step execution. ALWAYS save for workflows with sequential steps.\n\n"
        "ALWAYS SAVE WHEN:\n"
        "ALWAYS: Multi-step device workflows (power on → app switch → action)\n"
        "ALWAYS: Sequential execution with delays and conditional checks\n"
        "ALWAYS: User preference workflows ('cozy', 'movie night') - after getting preferences\n"
        "ALWAYS: Adapted routines from other rooms (after successful execution)\n"
        "ALWAYS: Fixed/corrected failed workflows\n"
        "NEVER: Simple single-step actions (lights, switches)\n\n"
        "NEVER: Unchanged replay (auto-skips duplicate)\n\n"
        "STEPS PARAMETER:\n"
        "NEW scenes: MUST provide steps array with full workflow\n"
        "UPDATES (reporting success of existing scene): steps are OPTIONAL - will be retrieved from memory\n"
        "If updating and steps omitted, existing steps will be used\n\n"
        "BEFORE SAVING - CHECK FOR DUPLICATES:\n"
        "Look at existing scenes for same room/intent. Update existing rather than create duplicate.\n"
        "If exists: Update steps, preserve entry_id\n"
        "If new: Create with stable entry_id (intent_room format)\n\n"
        "CONTENT MUST BE GENERIC:\n"
        "NEVER: Specific content in scene name ('play_matrix', 'play_taylor_swift')\n"
        "NEVER: Hard-coded song/movie/show names in steps\n"
        "ALWAYS: Generic workflow names ('play_media_gym', 'music_office')\n"
        "ALWAYS: Content passed as execution variable, not baked into scene\n\n"
        "STEP TYPES:\n"
        "service_call: Execute HA service with optional guards\n"
        "delay: Wait between actions for device readiness\n"
        "tool_call: Execute deterministic tool with result capture\n"
        "Guards (only_if_state): Skip step based on entity state/attribute checks\n\n"
        "Returns: {status, message, entry_id}"
    ),
    parameters=PARAMS,
    returns="dict with status and message",
    func=set_scene,
    can_run_parallel=True,
)
