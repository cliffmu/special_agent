"""Utilities for parsing and formatting Responses API output."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, List, Dict

try:
    from .session_helpers import generate_message_id
    from . import logging as log
    from . import performance
except ImportError:
    from utils.session_helpers import generate_message_id
    from utils import logging as log
    from utils import performance


def extract_function_calls(resp: Any) -> list:
    """Extract function_call items from Responses API output."""
    function_calls = []
    output = getattr(resp, "output", None)
    if output:
        for item in output:
            if getattr(item, "type", None) == "function_call":
                function_calls.append(item)
        if function_calls:
            return function_calls

    choices = getattr(resp, "choices", None)
    if choices:
        from types import SimpleNamespace

        for choice in choices:
            message = getattr(choice, "message", None)
            if not message:
                continue
            tool_calls = getattr(message, "tool_calls", None) or []
            for tool_call in tool_calls:
                func = getattr(tool_call, "function", None)
                name = getattr(func, "name", getattr(tool_call, "name", None))
                arguments = getattr(func, "arguments", getattr(tool_call, "arguments", "{}"))
                call_id = getattr(tool_call, "id", None)
                function_calls.append(
                    SimpleNamespace(
                        name=name,
                        arguments=arguments,
                        call_id=call_id,
                    )
                )
        return function_calls

    return []


def extract_final_text(resp: Any) -> str | None:
    """Extract final text response from Responses API output."""
    output = getattr(resp, "output", None)
    if output:
        for item in output:
            if getattr(item, "type", None) == "message":
                content_items = getattr(item, "content", [])
                if isinstance(content_items, list):
                    for content_item in content_items:
                        if getattr(content_item, 'type', None) == 'output_text':
                            return getattr(content_item, 'text', None)
                else:
                    return getattr(item, "content", None)

    choices = getattr(resp, "choices", None)
    if choices:
        for choice in choices:
            message = getattr(choice, "message", None)
            if message and getattr(message, "content", None):
                return message.content
    return None


def summarize_result(result: Any) -> str:
    """Summarize tool result for agent observation."""
    try:
        if isinstance(result, list):
            json_txt = json.dumps(result)
            if len(result) <= 5 and len(json_txt) <= 200:
                return json_txt
            head = ", ".join(map(str, result[:5]))
            return f"{len(result)} items: {head}{' …' if len(result) > 5 else ''}"

        if isinstance(result, dict):
            # Don't truncate tool results - agent needs to see the data
            json_txt = json.dumps(result, ensure_ascii=False)
            # Only truncate if extremely long (>2000 chars)
            if len(json_txt) <= 2000:
                return json_txt
            # For very long results, show first part
            return json_txt[:2000] + "... (truncated)"

        txt = str(result)
        return txt if len(txt) <= 200 else txt[:200] + " …"
    except Exception as err:
        return f"(summary error: {err})"


@dataclass
class ToolExecutionResult:
    """Result of tool execution phase."""
    messages: List[Dict]  # Function call outputs to add to history
    focus: Dict | None  # Updated focus state
    prompt_response: Dict | None  # If tool returned speak/confirm
    all_failed: bool  # True if every tool failed
    error_message: str | None  # Error to return to user


async def validate_and_execute_tools(
    function_calls: List[Any],
    spec_map: Dict[str, Any],
    tried_calls: set,
    hass: Any
) -> ToolExecutionResult:
    """Validate, execute tools in parallel, return structured results.
    
    Args:
        function_calls: List of function calls from LLM
        spec_map: Dict mapping tool names to ToolSpec objects
        tried_calls: Set of (name, canonical_args) tuples already tried
        hass: Home Assistant instance
        
    Returns:
        ToolExecutionResult with messages, focus, and prompt responses
    """
    # Phase 1: Validate all calls and check parallel execution rules
    tasks_to_run = []  # List of (call, spec, args) tuples
    validation_errors = []  # Track validation errors
    messages_to_add = []
    focus = None
    
    # Check if any tool cannot run in parallel
    non_parallel_tools = []
    for call in function_calls:
        if call.name in spec_map:
            spec = spec_map[call.name]
            if not spec.can_run_parallel:
                non_parallel_tools.append(call.name)
    
    # If we have multiple calls and one cannot run in parallel, drop the non-parallel ones
    if len(function_calls) > 1 and non_parallel_tools:
        log.warning(
            "Tool(s) %s cannot run in parallel. LLM called %d tools. "
            "Dropping non-parallel tools and executing parallel-capable tools.",
            non_parallel_tools, len(function_calls)
        )
        # Drop non-parallel tools, keep parallel-capable ones
        dropped_calls = [fc for fc in function_calls if fc.name in non_parallel_tools]
        function_calls = [fc for fc in function_calls if fc.name not in non_parallel_tools]

        # Add error messages for dropped non-parallel tools
        for dropped_call in dropped_calls:
            parallel_tools = [fc.name for fc in function_calls]
            validation_errors.append((
                dropped_call,
                f"Error: Tool '{dropped_call.name}' cannot run in parallel with other tools ({parallel_tools}). "
                f"First complete the parallel searches, then call '{dropped_call.name}' in the next turn."
            ))

    # Now validate each call
    for call in function_calls:
        call_name = call.name
        raw_json = call.arguments or "{}"
        canonical = (call_name, json.dumps(json.loads(raw_json), sort_keys=True))

        # duplicate guard
        if canonical in tried_calls:
            log.debug("Duplicate call blocked: %s", canonical)
            validation_errors.append((call, "Error: Duplicate call. Already tried this exact query."))
            continue
        tried_calls.add(canonical)

        # validate
        try:
            spec = spec_map[call_name]
            args = json.loads(raw_json)
            # Use custom validation if provided, otherwise skip validation
            if spec.validate:
                args = spec.validate(args)
            tasks_to_run.append((call, spec, args))
        except Exception as err:
            log.debug("Validation error: %s", err)
            validation_errors.append((call, f"Error: Tool arguments were invalid: {err}"))
            continue

    # Add validation errors to messages immediately
    for call, error_msg in validation_errors:
        messages_to_add.append({
            "type": "function_call_output",
            "call_id": call.call_id,
            "output": error_msg,
            "id": generate_message_id("fc")
        })

    # Phase 2: Execute all valid calls in parallel
    async def execute_tool(call, spec, args):
        """Execute a single tool and return (call, spec, args, result, is_error)"""
        call_name = spec.name
        log.debug("Action: %s %s", call_name, args)
        try:
            async with performance.track_operation(
                f"tool_{call_name}",
                metadata={"args": str(args)[:200]}  # Truncate long args
            ):
                if hass and "hass" in inspect.signature(spec.func).parameters:
                    call_args = {"hass": hass, **args}
                    log.debug("Tool_Input[%s]: %s", call_name, call_args)
                    result = await spec.func(**call_args)
                else:
                    call_args = args
                    log.debug("Tool_Input[%s]: %s", call_name, call_args)
                    result = await spec.func(**call_args)
            log.debug("Tool_Result[%s]: %s", call_name, result)
            return (call, spec, args, result, False)
        except Exception as err:
            log.error("Tool execution failed: %s", err)
            return (call, spec, args, f"Error: Tool failed: {err}", True)

    # Run all tools in parallel
    if tasks_to_run:
        if len(tasks_to_run) > 1:
            # Track parallel execution group
            async with performance.track_operation(
                f"parallel_tools_{len(tasks_to_run)}",
                metadata={"tools": [spec.name for _, spec, _ in tasks_to_run]}
            ):
                results = await asyncio.gather(
                    *[execute_tool(call, spec, args) for call, spec, args in tasks_to_run],
                    return_exceptions=False
                )
        else:
            # Single tool, no parallel tracking needed
            results = await asyncio.gather(
                *[execute_tool(call, spec, args) for call, spec, args in tasks_to_run],
                return_exceptions=False
            )
    else:
        results = []

    # Phase 3: Process results
    all_results_success = len(validation_errors) == 0
    has_prompt_response = False
    prompt_result = None
    errors_encountered: List[str] = []

    for call, spec, args, result, is_error in results:
        call_name = spec.name

        if is_error:
            all_results_success = False
            error_text = result if isinstance(result, str) else str(result)
            errors_encountered.append(error_text)
            messages_to_add.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": result,  # Already formatted as error string
                "id": generate_message_id("fc")
            })
            continue

        # Update focus tracking
        if call_name == "control_device":
            focus = {
                "targets": args["data"].get("entity_id", []),
                "action": args["service"],
            }
        elif isinstance(result, dict) and result.get("focus"):
            focus = result["focus"]

        # Append function call result in Responses API format
        result_str = summarize_result(result)
        messages_to_add.append({
            "type": "function_call_output",
            "call_id": call.call_id,
            "output": result_str,
            "id": generate_message_id("fc")
        })

        # Check if any result has a prompt response
        if isinstance(result, dict) and "speak" in result:
            if has_prompt_response:
                # Multiple tools returned "speak" in parallel - prioritize deterministic order
                log.warning(
                    "Multiple tools returned 'speak' in parallel: %s and %s",
                    prompt_result.get("kind", "unknown"),
                    result.get("kind", "unknown")
                )
                result_kind = result.get("kind", "")
                current_kind = prompt_result.get("kind", "")
                if result_kind == "confirm" or (result_kind == "clarify" and current_kind == "response"):
                    prompt_result = result
            else:
                has_prompt_response = True
                prompt_result = result

    # Determine if all tools failed
    all_failed = errors_encountered and len(errors_encountered) == len(results)
    error_message = errors_encountered[0] if all_failed else None
    
    if all_failed:
        log.warning("All tool calls failed in this iteration: %s", error_message)

    return ToolExecutionResult(
        messages=messages_to_add,
        focus=focus,
        prompt_response=prompt_result if has_prompt_response else None,
        all_failed=all_failed,
        error_message=error_message
    )

