"""Utilities for executing saved or inline sequences."""
from __future__ import annotations

import asyncio
import inspect
import re
import time
from typing import Any, Dict, List, Tuple

from . import logging as log
from .data_sources import call_service_tracked
from .tool_registry import ToolSpec, get_sequence_safe_tool_specs


def _substitute_vars(obj: Any, vars: Dict) -> Any:
    """Replace ${variable} placeholders in strings, dicts, and lists."""

    if isinstance(obj, str):
        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return str(vars.get(var_name, ""))

        return re.sub(r"\$\{([a-zA-Z0-9_?]+)\}", replacer, obj)
    if isinstance(obj, dict):
        return {k: _substitute_vars(v, vars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_vars(item, vars) for item in obj]
    return obj


async def _call_service(hass: Any, service: str, data: Dict) -> None:
    """Call a Home Assistant service (tracked in data_sources wrapper)."""
    domain, name = service.split(".", 1)
    await call_service_tracked(hass, domain, name, data, blocking=True)


async def _wait_state(
    hass: Any,
    entity_id: str,
    in_states: List[str] | None = None,
    not_in: List[str] | None = None,
    attr: str | None = None,
    equals: Any | None = None,
    timeout: int = 10,
) -> bool:
    """Wait for an entity to reach the desired state."""

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


def _prepare_tool_kwargs(spec: ToolSpec, args: Dict[str, Any], hass: Any | None) -> Dict[str, Any]:
    """Prepare kwargs for invoking a tool, injecting hass if supported."""

    kwargs = dict(args)

    if hass is None:
        return kwargs

    try:
        signature = inspect.signature(spec.func)
        if "hass" in signature.parameters and "hass" not in kwargs:
            kwargs["hass"] = hass
    except (TypeError, ValueError):  # pragma: no cover - builtins without signature
        if "hass" not in kwargs:
            kwargs["hass"] = hass

    return kwargs


def _extract_path_value(result: Any, path: str | None) -> Tuple[Any, bool]:
    """Extract a nested value using dotted path syntax."""

    if not path:
        return result, True

    current = result
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(part)
            except (TypeError, ValueError):
                return None, False
            if idx < 0 or idx >= len(current):
                return None, False
            current = current[idx]
        else:
            return None, False
    return current, True


def _evaluate_expectation(expect: Dict[str, Any], result: Any) -> Tuple[bool, str | None, Any]:
    """Validate expectation config against tool result."""

    path = expect.get("path")
    value, found = _extract_path_value(result, path)

    if expect.get("exists") is True and not found:
        return False, f"Expected value at path '{path}'", value
    if expect.get("exists") is False and found and value is not None:
        return False, f"Expected no value at path '{path}', but found {value}", value

    if "equals" in expect and value != expect["equals"]:
        return False, f"Expected {expect['equals']} at '{path or 'result'}', got {value}", value
    if "not_equals" in expect and value == expect["not_equals"]:
        return False, f"Expected value at '{path or 'result'}' to differ from {expect['not_equals']}", value

    if "contains" in expect:
        expected_item = expect["contains"]
        if isinstance(value, (list, tuple, set)):
            if expected_item not in value:
                return False, f"Expected list at '{path or 'result'}' to contain {expected_item}", value
        elif isinstance(value, str):
            if str(expected_item) not in value:
                return False, f"Expected string at '{path or 'result'}' to contain {expected_item}", value
        else:
            return False, f"Cannot apply 'contains' expectation to value at '{path or 'result'}'", value

    return True, None, value


async def run_sequence(
    sequence_ref: str | None = None,
    sequence: Dict | None = None,
    vars: Dict | None = None,
    timeout: int = 30,
    hass: Any | None = None,
) -> Dict[str, Any]:
    """Execute a sequence of steps with variable substitution and guards."""

    log.debug("run_sequence: ref=%s, inline=%s", sequence_ref, bool(sequence))

    # Load sequence from scene memory if ref provided
    if sequence_ref:
        try:
            from .vector_index import async_search_scenes

            results = await async_search_scenes(sequence_ref, area=None, k=1, hass=hass)
            if results and results[0].get("steps"):
                steps = results[0]["steps"]
                log.debug("Loaded scene steps from memory: %s (%d steps)", sequence_ref, len(steps))
            else:
                log.warning("Scene ref '%s' not found in memory", sequence_ref)
                steps = []
        except Exception as err:  # pragma: no cover - runtime guard
            log.error("Failed to load scene ref '%s': %s", sequence_ref, err)
            steps = []
    else:
        steps = (sequence or {}).get("steps", [])

    sequence_tool_specs = get_sequence_safe_tool_specs()

    vars = vars or {}
    results: List[Dict[str, Any]] = []
    started = time.monotonic()

    for step in steps:
        if time.monotonic() - started > timeout:
            return {
                "result": "failed",
                "steps": results,
                "total_steps": len(steps),
                "completed_steps": len(results),
                "error": "Sequence timeout exceeded",
            }

        step_type = step.get("type")
        step_result: Dict[str, Any] = {
            "name": step.get("name") or step.get("service") or step_type,
            "status": "pending",
            "type": step_type,
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
            if only_if_state and hass:
                entity = _substitute_vars(only_if_state["entity_id"], vars)
                state_obj = hass.states.get(entity)
                if state_obj:
                    attribute_name = only_if_state.get("attribute")
                    if attribute_name:
                        current_value = state_obj.attributes.get(attribute_name)

                        if "equals" in only_if_state and current_value != only_if_state["equals"]:
                            step_result["status"] = "skipped"
                            results.append(step_result)
                            continue

                        if "not_equals" in only_if_state and current_value == only_if_state["not_equals"]:
                            step_result["status"] = "skipped"
                            results.append(step_result)
                            continue

                        if "in" in only_if_state and current_value not in only_if_state["in"]:
                            step_result["status"] = "skipped"
                            results.append(step_result)
                            continue

                        if "not_in" in only_if_state and current_value in only_if_state["not_in"]:
                            step_result["status"] = "skipped"
                            results.append(step_result)
                            continue
                    else:
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
                if hass is None:
                    raise RuntimeError("Home Assistant instance required for service_call")
                await _call_service(hass, service, data)
                step_result["status"] = "ok"

                post_condition = step.get("post_condition")
                if post_condition and hass:
                    try:
                        from ..agent_core import SCENE_MEMORY_CONFIG
                    except ImportError:  # pragma: no cover - direct execution fallback
                        from agent_core import SCENE_MEMORY_CONFIG  # type: ignore

                    default_timeout_ms = SCENE_MEMORY_CONFIG.get("post_condition_timeout_ms", 4000)
                    timeout_ms = post_condition.get("timeout_ms", default_timeout_ms)
                    timeout_seconds = timeout_ms / 1000.0

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
                            timeout=int(timeout_seconds),
                        )

                        if not success:
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
                if hass is None:
                    raise RuntimeError("Home Assistant instance required for wait_state")
                success = await _wait_state(
                    hass,
                    entity,
                    in_states=step.get("in"),
                    not_in=step.get("not_in"),
                    attr=step.get("attr"),
                    equals=step.get("equals"),
                    timeout=wait_timeout,
                )
                if success:
                    step_result["status"] = "ok"
                else:
                    step_result["status"] = "timeout"
                    state_obj = hass.states.get(entity) if hass else None
                    current = state_obj.state if state_obj else "unknown"
                    expected = step.get("in") or step.get("equals") or step.get("not_in")
                    step_result["error"] = (
                        f"Timeout after {wait_timeout}s waiting for {entity}, current state: {current}, expected: {expected}"
                    )

            elif step_type == "delay":
                seconds = step.get("seconds", 0)
                await asyncio.sleep(seconds)
                step_result["status"] = "ok"

            elif step_type == "tool_call":
                tool_name = step.get("tool")
                spec = sequence_tool_specs.get(tool_name or "")
                if not spec:
                    step_result["status"] = "error"
                    step_result["error"] = f"Tool '{tool_name}' not allowed in sequences"
                    log.error("run_sequence: tool '%s' is not sequence-safe", tool_name)
                else:
                    step_result["tool"] = tool_name
                    substituted_args = _substitute_vars(step.get("args", {}), vars)
                    kwargs = _prepare_tool_kwargs(spec, substituted_args, hass)
                    tool_result = await spec.func(**kwargs)
                    step_result["tool_result"] = tool_result
                    result_var = step.get("result_var")
                    stored_value = tool_result
                    if result_var:
                        result_path = step.get("result_path")
                        value, found = _extract_path_value(tool_result, result_path)
                        if result_path and not found:
                            step_result["status"] = "error"
                            step_result["error"] = f"result_path '{result_path}' not found in tool result"
                            step_result["result_path"] = result_path
                        else:
                            stored_value = value
                            vars[result_var] = stored_value
                            step_result["stored_var"] = result_var
                            step_result["stored_value"] = stored_value
                            if result_path:
                                step_result["result_path"] = result_path
                    expect = step.get("expect")
                    if step_result.get("status") != "error" and expect:
                        ok, message, observed = _evaluate_expectation(expect, tool_result)
                        if ok:
                            step_result["expect_verified"] = True
                        else:
                            step_result["status"] = "error"
                            step_result["error"] = message
                            step_result["expect_failed"] = True
                            step_result["observed"] = observed
                    if step_result["status"] != "error":
                        step_result["status"] = "ok"

            elif step_type == "if":
                condition = step.get("when")
                is_true = bool(vars.get(condition.strip("${}"), False)) if condition else False

                if is_true and "then" in step:
                    if hass is None:
                        raise RuntimeError("Home Assistant instance required for conditional service execution")
                    for sub_step in step["then"]:
                        if sub_step["type"] == "service_call":
                            service = sub_step["service"]
                            data = _substitute_vars(sub_step.get("data", {}), vars)
                            await _call_service(hass, service, data)

                step_result["status"] = "ok"

            else:
                step_result["status"] = "error"
                step_result["error"] = f"Unknown step type: {step_type}"

        except Exception as err:  # pragma: no cover - runtime guard
            log.error("Step %s failed: %s", step_result["name"], err)
            step_result["status"] = "error"
            step_result["error"] = str(err)

        results.append(step_result)

        if step_result.get("post_condition_failed") or step_result.get("expect_failed"):
            failure_reason = (
                "post-condition failure" if step_result.get("post_condition_failed") else "tool expectation failure"
            )
            log.warning(
                "Aborting sequence due to %s on step: %s",
                failure_reason,
                step_result.get("name"),
            )
            return {
                "result": "failed",
                "steps": results,
                "total_steps": len(steps),
                "completed_steps": len(results),
                "error": f"Sequence aborted due to {failure_reason}",
            }

    all_ok = all(s["status"] in ("ok", "skipped") for s in results)
    any_error = any(s["status"] == "error" for s in results)
    result = "completed" if all_ok else ("failed" if any_error else "partial")

    return {
        "result": result,
        "steps": results,
        "total_steps": len(steps),
        "completed_steps": len(results),
    }
