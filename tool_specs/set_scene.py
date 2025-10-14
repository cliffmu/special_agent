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
            from ..utils.vector_index import async_upsert_scene
            import time
            
            # Build memory entry
            entry_id = f"{intent}_{area or 'global'}_{int(time.time())}"
            
            # Calculate confidence based on outcome
            # Start with base confidence and adjust based on outcomes over time
            confidence = 0.5  # Default for new entries
            if outcome == "success":
                confidence = 0.8
            elif outcome == "fail":
                confidence = 0.3
            elif outcome == "corrected":
                confidence = 0.6
            
            # Normalize steps to proper run_sequence format
            normalized_steps = []
            for s in steps:
                if isinstance(s, str):
                    # String step - skip, can't normalize
                    log.warning("String step in set_scene, skipping: %s", s[:50])
                    continue
                elif not isinstance(s, dict):
                    log.warning("Invalid step type: %s", type(s))
                    continue
                
                # Normalize dict step
                normalized = {}
                
                # Determine step type
                if "wait_seconds" in s or s.get("type") == "wait":
                    # Delay step
                    normalized["type"] = "delay"
                    normalized["seconds"] = s.get("wait_seconds") or s.get("seconds", 0)
                elif "service" in s:
                    # Service call step
                    normalized["type"] = "service_call"
                    normalized["service"] = s["service"]
                    
                    # Normalize data field
                    if "data" in s:
                        normalized["data"] = s["data"]
                    elif "entity_id" in s:
                        # Build data from entity_id + other fields
                        data = {"entity_id": s["entity_id"]}
                        if "source" in s:
                            data["source"] = s["source"]
                        if "params" in s:
                            # Try to parse params
                            if isinstance(s["params"], dict):
                                data.update(s["params"])
                        normalized["data"] = data
                    elif "entity" in s:
                        # Has friendly name - can't use, log warning
                        log.warning("Step has 'entity' with friendly name, need entity_id: %s", s)
                        continue
                    else:
                        normalized["data"] = {}
                elif s.get("type") == "delay":
                    # Already proper delay format
                    normalized = s
                elif s.get("type") == "service_call":
                    # Already proper service_call format
                    normalized = s
                else:
                    # Unknown format
                    log.warning("Unknown step format: %s", s)
                    continue
                
                normalized_steps.append(normalized)
            
            # Build summary from normalized steps
            step_types = [s.get("type", "unknown") for s in normalized_steps]
            summary = f"{len(normalized_steps)} steps: {', '.join(step_types[:3])}"
            if len(step_types) > 3:
                summary += f", +{len(step_types) - 3} more"
            
            # Build strategy hint
            strategy = notes or f"Learned from {outcome} execution"
            
            entry = {
                "id": entry_id,
                "intent": intent,
                "area_hint": area,
                "summary": summary,
                "steps": normalized_steps,  # Use normalized steps
                "strategy": strategy,
                "confidence": confidence,
                "updated_at": time.time(),
            }
            
            # Update store and rebuild index
            await async_upsert_scene(entry, hass=hass)
            
            log.info("Scene learning recorded: intent=%s, outcome=%s, id=%s", 
                     intent, outcome, entry_id)
            
            return {
                "status": "ok", 
                "message": f"Scene '{intent}' learning recorded with confidence {confidence:.2f}",
                "entry_id": entry_id
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

