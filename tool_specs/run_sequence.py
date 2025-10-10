"""Tool for executing multi-step sequences with waits and guards."""

import asyncio
import time
from typing import Any, Dict, List
import re

from ..agent_core import ToolSpec
from ..utils import logging as log

PARAMS = {
    "type": "object",
    "properties": {
        "sequence_ref": {
            "type": "string",
            "description": "Reference to saved sequence in preferences (e.g., 'play_on_screen.v1')"
        },
        "sequence": {
            "type": "object",
            "description": "Inline sequence definition with steps array"
        },
        "vars": {
            "type": "object",
            "description": "Variables for ${substitution} in sequence steps"
        },
        "timeout": {
            "type": "integer",
            "description": "Overall timeout in seconds",
            "default": 30
        }
    },
    "required": []
}

def _substitute_vars(obj: Any, vars: Dict) -> Any:
    """Replace ${variable} placeholders in strings, dicts, and lists."""
    if isinstance(obj, str):
        def replacer(match):
            var_name = match.group(1)
            return str(vars.get(var_name, ""))
        return re.sub(r'\$\{([a-zA-Z0-9_?]+)\}', replacer, obj)
    elif isinstance(obj, dict):
        return {k: _substitute_vars(v, vars) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_substitute_vars(item, vars) for item in obj]
    return obj

async def _call_service(hass: Any, service: str, data: Dict) -> None:
    """Call a Home Assistant service."""
    domain, name = service.split(".", 1)
    await hass.services.async_call(domain, name, data, blocking=True)

async def _wait_state(
    hass: Any,
    entity_id: str,
    in_states: List[str] | None = None,
    not_in: List[str] | None = None,
    attr: str | None = None,
    equals: Any | None = None,
    timeout: int = 10
) -> bool:
    """Wait for entity to reach desired state."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        state_obj = hass.states.get(entity_id)
        if state_obj:
            value = state_obj.state if not attr else state_obj.attributes.get(attr)
            
            if in_states and value in in_states:
                return True
            if not_in and value not in not_in:
                return True
            if equals is not None and value == equals:
                return True
        
        await asyncio.sleep(0.5)
    return False

async def run_sequence(
    sequence_ref: str | None = None,
    sequence: Dict | None = None,
    vars: Dict | None = None,
    timeout: int = 30,
    hass: Any | None = None
) -> Dict[str, Any]:
    """
    Execute a sequence of steps with variable substitution.
    
    Steps can include:
    - service_call: Call HA service (blocking, waits for completion)
    - wait_state: Wait for entity state change (polls every 0.5s)
    - delay: Simple time delay
    - if: Conditional execution
    
    Returns per-step status and overall result.
    """
    log.debug(f"run_sequence: ref={sequence_ref}, inline={bool(sequence)}")
    
    # Load sequence from preferences if ref provided
    if sequence_ref:
        from .get_preferences import get_preferences
        result = await get_preferences(namespace="sequences", key=sequence_ref, hass=hass)
        seq_data = result.get("data", {})
        steps = seq_data.get("steps", [])
    else:
        steps = (sequence or {}).get("steps", [])
    
    vars = vars or {}
    results = []
    started = time.monotonic()
    
    for step in steps:
        if time.monotonic() - started > timeout:
            return {
                "status": "error",
                "result": "timeout",
                "steps": results,
                "error": "Sequence timeout exceeded"
            }
        
        step_result = {
            "name": step.get("name") or step.get("service") or step.get("type"),
            "status": "pending"
        }
        
        try:
            # Check conditional guards
            if_guard = step.get("only_if")
            if if_guard and not vars.get(if_guard.strip("${}"), False):
                step_result["status"] = "skipped"
                results.append(step_result)
                continue
            
            # Check state guards
            only_if_state = step.get("only_if_state")
            if only_if_state:
                entity = _substitute_vars(only_if_state["entity_id"], vars)
                state_obj = hass.states.get(entity)
                if state_obj:
                    current_state = state_obj.state
                    in_states = only_if_state.get("in")
                    not_in_states = only_if_state.get("not_in")
                    
                    if in_states and current_state not in in_states:
                        step_result["status"] = "skipped"
                        results.append(step_result)
                        continue
                    if not_in_states and current_state in not_in_states:
                        step_result["status"] = "skipped"
                        results.append(step_result)
                        continue
            
            # Execute step based on type
            step_type = step["type"]
            
            if step_type == "service_call":
                service = step["service"]
                data = _substitute_vars(step.get("data", {}), vars)
                await _call_service(hass, service, data)
                step_result["status"] = "ok"
            
            elif step_type == "wait_state":
                entity = _substitute_vars(step["entity_id"], vars)
                success = await _wait_state(
                    hass,
                    entity,
                    in_states=step.get("in"),
                    not_in=step.get("not_in"),
                    attr=step.get("attr"),
                    equals=step.get("equals"),
                    timeout=step.get("timeout", 10)
                )
                step_result["status"] = "ok" if success else "timeout"
            
            elif step_type == "delay":
                seconds = step.get("seconds", 0)
                await asyncio.sleep(seconds)
                step_result["status"] = "ok"
            
            elif step_type == "if":
                # Conditional execution
                condition = step.get("when")
                is_true = bool(vars.get(condition.strip("${}"), False)) if condition else False
                
                if is_true and "then" in step:
                    # Execute then branch (recursively handle nested steps)
                    for sub_step in step["then"]:
                        if sub_step["type"] == "service_call":
                            service = sub_step["service"]
                            data = _substitute_vars(sub_step.get("data", {}), vars)
                            await _call_service(hass, service, data)
                
                step_result["status"] = "ok"
            
            else:
                step_result["status"] = "unknown_type"
        
        except Exception as e:
            log.error(f"Step {step_result['name']} failed: {e}")
            step_result["status"] = "error"
            step_result["error"] = str(e)
        
        results.append(step_result)
    
    # Determine overall result
    all_ok = all(s["status"] in ("ok", "skipped") for s in results)
    any_error = any(s["status"] == "error" for s in results)
    
    overall = "completed" if all_ok else ("failed" if any_error else "partial")
    
    return {
        "status": "ok",
        "result": overall,
        "steps": results,
        "error": None if all_ok else "Some steps failed"
    }

SPEC = ToolSpec(
    name="run_sequence",
    description=(
        "Execute a sequence of steps (service calls, waits, delays, conditions). "
        "Each step can have guards (only_if_state) and waits ensure previous steps complete before next. "
        "Use for complex multi-step operations like media playback or scene activation with timing."
    ),
    parameters=PARAMS,
    returns="dict with result and per-step statuses",
    func=run_sequence
)

