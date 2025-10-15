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
    
    # Load sequence from scene memory if ref provided
    if sequence_ref:
        try:
            from ..utils.vector_index import async_search_scenes
            # Try to find scene by intent matching the ref
            results = await async_search_scenes(sequence_ref, area=None, k=1, hass=hass)
            if results and results[0].get("steps"):
                steps = results[0]["steps"]
                log.debug("Loaded scene steps from memory: %s (%d steps)", 
                         sequence_ref, len(steps))
            else:
                log.warning("Scene ref '%s' not found in memory", sequence_ref)
                steps = []
        except Exception as err:
            log.error("Failed to load scene ref '%s': %s", sequence_ref, err)
            steps = []
    else:
        steps = (sequence or {}).get("steps", [])
    
    vars = vars or {}
    results = []
    started = time.monotonic()
    
    for step in steps:
        if time.monotonic() - started > timeout:
            return {
                "status": "error",
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
                
                # Post-condition verification (optional)
                post_condition = step.get("post_condition")
                if post_condition:
                    # Get timeout (default from config or step override)
                    try:
                        from ..agent_core import SCENE_MEMORY_CONFIG
                        default_timeout_ms = SCENE_MEMORY_CONFIG.get("post_condition_timeout_ms", 4000)
                    except ImportError:
                        default_timeout_ms = 4000
                    
                    timeout_ms = post_condition.get("timeout_ms", default_timeout_ms)
                    timeout_seconds = timeout_ms / 1000.0
                    
                    # Poll entity state
                    entity_id = post_condition.get("entity_id")
                    expected_state = post_condition.get("state")
                    expected_attr = post_condition.get("attribute")
                    expected_value = post_condition.get("value")
                    
                    if entity_id and (expected_state or expected_attr):
                        success = await _wait_state(
                            hass,
                            entity_id,
                            in_states=[expected_state] if expected_state else None,
                            attr=expected_attr,
                            equals=expected_value,
                            timeout=int(timeout_seconds)
                        )
                        
                        if not success:
                            # Post-condition failed
                            state_obj = hass.states.get(entity_id)
                            current = state_obj.state if state_obj else "unknown"
                            step_result["status"] = "error"
                            step_result["error"] = (
                                f"Post-condition failed: {entity_id} expected {expected_state or expected_value}, "
                                f"got {current} after {timeout_ms}ms"
                            )
                            step_result["post_condition_failed"] = True
                        else:
                            step_result["post_condition_verified"] = True
            
            elif step_type == "wait_state":
                entity = _substitute_vars(step["entity_id"], vars)
                wait_timeout = step.get("timeout", 10)
                success = await _wait_state(
                    hass,
                    entity,
                    in_states=step.get("in"),
                    not_in=step.get("not_in"),
                    attr=step.get("attr"),
                    equals=step.get("equals"),
                    timeout=wait_timeout
                )
                if success:
                    step_result["status"] = "ok"
                else:
                    step_result["status"] = "timeout"
                    # Add current state for context
                    state_obj = hass.states.get(entity)
                    current = state_obj.state if state_obj else "unknown"
                    expected = step.get("in") or step.get("equals") or step.get("not_in")
                    step_result["error"] = f"Timeout after {wait_timeout}s waiting for {entity}, current state: {current}, expected: {expected}"
            
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
                step_result["status"] = "error"
                step_result["error"] = f"Unknown step type: {step_type}"
        
        except Exception as e:
            log.error(f"Step {step_result['name']} failed: {e}")
            step_result["status"] = "error"
            step_result["error"] = str(e)
        
        results.append(step_result)
        
        # Abort sequence if post-condition failed
        if step_result.get("post_condition_failed"):
            log.warning("Aborting sequence due to post-condition failure on step: %s", 
                       step_result.get("name"))
            return {
                "result": "failed",
                "steps": results,
                "total_steps": len(steps),
                "completed_steps": len(results),
                "error": "Sequence aborted due to post-condition failure"
            }
    
    # Determine overall result
    all_ok = all(s["status"] in ("ok", "skipped") for s in results)
    any_error = any(s["status"] == "error" for s in results)
    result = "completed" if all_ok else ("failed" if any_error else "partial")
    
    # Return step results for agent to interpret
    return {
        "result": result,
        "steps": results,
        "total_steps": len(steps),
        "completed_steps": len(results)
    }

SPEC = ToolSpec(
    name="run_sequence",
    description=(
        "Execute multi-step sequence with service calls, delays, and guards. Guards skip steps instantly (~1ms check).\n\n"
        "STEP FORMATS:\n"
        "1) Service: {\"type\":\"service_call\",\"service\":\"light.turn_on\",\"data\":{\"entity_id\":\"...\"}} \n"
        "2) Delay: {\"type\":\"delay\",\"seconds\":8} \n"
        "3) Guarded service (skip if device ready): "
        "{\"type\":\"service_call\",...,\"only_if_state\":{\"entity_id\":\"media_player.tv\",\"not_in\":[\"idle\",\"playing\"]}}\n"
        "4) Guarded delay (skip wait if not needed): "
        "{\"type\":\"delay\",\"seconds\":8,\"only_if_state\":{\"entity_id\":\"...\",\"not_in\":[\"idle\"]}}\n\n"
        "GUARDS (checked internally, zero LLM overhead):\n"
        "- only_if_state: Skip step if entity in/not_in specific states\n"
        "- Checked using direct hass.states.get() (~1-2ms)\n"
        "- Returns status='skipped' for guarded steps\n"
        "- Example: Skip turn_on + 8s delay if TV already playing\n\n"
        "Returns: dict with steps[], each with status='ok'/'skipped'/'error'/'timeout'"
    ),
    parameters=PARAMS,
    returns="dict(result, steps, total_steps, completed_steps)",
    func=run_sequence
)

