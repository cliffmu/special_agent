"""LLM client utilities for API calling, error handling, and metrics extraction."""

from __future__ import annotations

import logging
import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

_LOGGER = logging.getLogger(__package__)

try:  # AsyncOpenAI only in openai>=1.0
    from openai import AsyncOpenAI  # type: ignore
except Exception:  # pragma: no cover - openai optional
    AsyncOpenAI = None  # type: ignore

try:
    from .response_utils import extract_function_calls, extract_final_text
    from . import logging as log
    from .constants import normalize_reasoning_effort
except ImportError:
    from utils.response_utils import extract_function_calls, extract_final_text
    from utils import logging as log
    from utils.constants import normalize_reasoning_effort

_CLIENT: Any | None = None


@dataclass
class LLMResponse:
    """Normalized LLM response across different APIs."""
    output: List[Dict]  # Raw output messages in Responses API format
    function_calls: List[Any]  # Extracted function calls
    final_text: str | None  # Final text response if any
    usage: Dict  # Token usage information
    reasoning_count: int  # Number of reasoning blocks (for extended thinking)


async def get_async_client(hass: Any | None = None) -> Any:
    """Return a cached AsyncOpenAI client, creating it in executor if needed."""
    if AsyncOpenAI is None:  # pragma: no cover - openai optional
        raise RuntimeError("openai package not available")

    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    if hass is not None:
        _CLIENT = await hass.async_add_executor_job(AsyncOpenAI)
    else:  # used in unit tests without hass
        _CLIENT = AsyncOpenAI()
    return _CLIENT


async def call_llm(
    client: Any,
    messages: List[Dict],
    tools: List[Dict],
    model: str,
    reasoning_effort: str,
    depth: int,
    *,
    fast_mode: bool = False,
) -> LLMResponse:
    """Call LLM using OpenAI Responses API and return normalized response.
    
    Args:
        client: AsyncOpenAI client instance
        messages: Conversation history
        tools: Tool specifications in OpenAI format (nested with 'function' key)
        model: Model name (e.g., "gpt-5", "gpt-4")
        reasoning_effort: Reasoning level ("minimal", "low", "medium", "high")
        depth: Current loop depth (for logging)
        
    Returns:
        LLMResponse with normalized output
    """
    # Responses API uses 'instructions' instead of system message
    # Extract system from messages if present
    instructions = None
    input_messages = messages
    if messages and messages[0].get("role") == "system":
        instructions = messages[0].get("content")
        input_messages = messages[1:]
    
    # Responses API expects flattened tool format: {type, name, description, parameters}
    # Convert from Chat format: {type, function: {name, description, parameters}}
    flattened_tools = []
    for tool in tools:
        if tool.get("type") == "function" and "function" in tool:
            func = tool["function"]
            flattened = {
                "type": "function",
                "name": func.get("name"),
                "description": func.get("description"),
                "parameters": func.get("parameters", {}),
            }
            if "strict" in func:
                flattened["strict"] = func["strict"]
            flattened_tools.append(flattened)
        else:
            # Already flattened or different type
            flattened_tools.append(tool)
    
    # Add built-in web_search
    tools_with_search = flattened_tools + [{"type": "web_search"}]
    
    # An explicit default tier makes the off switch override project Fast settings.
    tier = "fast" if fast_mode else "default"
    effort = normalize_reasoning_effort(model, reasoning_effort)
    started = time.perf_counter()
    fields = {"model": model, "effort": effort, "requested_tier": tier,
              "iteration": depth + 1, "source": "agent_loop", "backend": "responses_api"}
    reasoning = {"effort": effort}
    if log.trace_enabled():
        reasoning["summary"] = "auto"
    if depth == 0:
        available_tools = [
            tool.get("name") if tool.get("type") == "function" else tool.get("type")
            for tool in tools_with_search
        ]
        # Keep the whole catalog visible without exceeding trace payload bounds.
        for offset in range(0, len(available_tools), 20):
            log.trace_detail("agent_tools", payload={"tools": available_tools[offset:offset + 20]},
                             phase="available" if offset == 0 else "continued",
                             tool_count=len(available_tools),
                             remaining=max(0, len(available_tools) - offset - 20), **fields)
    log.activity("model", phase="sent", message_count=len(input_messages),
                 tool_count=len(tools_with_search), **fields)
    try:
        resp = await client.responses.create(
            model=model,
            instructions=instructions,
            input=input_messages,
            tools=tools_with_search,
            tool_choice="auto",
            reasoning=reasoning,
            service_tier=tier,
        )
    except (Exception, asyncio.CancelledError) as error:
        http_status = getattr(error, "status_code", None)
        error_fields = {"http_status": http_status} if type(http_status) is int else {}
        log.activity("model", phase="failed", **fields,
                     status="cancelled" if isinstance(error, asyncio.CancelledError) else "error",
                     error_type=type(error).__name__, elapsed_ms=round((time.perf_counter() - started) * 1000),
                     **error_fields)
        raise
    
    # Extract response data
    response_output = list(getattr(resp, "output", []))
    function_calls = extract_function_calls(resp)
    final_text = extract_final_text(resp)
    
    # Extract metrics
    reasoning_count = sum(1 for msg in response_output if getattr(msg, "type", None) == "reasoning")
    web_searches = [item for item in response_output if getattr(item, "type", None) == "web_search_call"]
    response_id = log.device_correlation(getattr(resp, "id", None))
    
    usage_dict = {}
    if hasattr(resp, 'usage') and resp.usage:
        usage_dict = {
            "input_tokens": getattr(resp.usage, 'input_tokens', None),
            "output_tokens": getattr(resp.usage, 'output_tokens', None),
            "cached_tokens": getattr(getattr(resp.usage, 'input_tokens_details', None), 'cached_tokens', None),
            "total_tokens": getattr(resp.usage, 'total_tokens', None),
        }
        # Preserve performance CSV's historical column names.
        usage_dict["prompt_tokens"] = usage_dict["input_tokens"]
        usage_dict["completion_tokens"] = usage_dict["output_tokens"]
        log.debug("LLM tokens: prompt=%s, completion=%s, total=%s, reasoning_blocks=%d", 
                 usage_dict.get("prompt_tokens", 'N/A'),
                 usage_dict.get("completion_tokens", 'N/A'),
                 usage_dict.get("total_tokens", 'N/A'),
                 reasoning_count)
    
    registered_names = {tool.get("name") for tool in flattened_tools if tool.get("type") == "function"}
    log.activity("model", phase="received", **fields,
                 response_id=response_id,
                 status=getattr(resp, "status", None) or "returned",
                 effective_tier=getattr(resp, "service_tier", None) or "unknown",
                 elapsed_ms=round((time.perf_counter() - started) * 1000),
                 function_count=len(function_calls),
                 function_names=[call.name if call.name in registered_names else "unknown" for call in function_calls],
                 decision="call_tools" if function_calls else "text_response" if final_text else "no_action",
                 reasoning_count=reasoning_count, web_search_count=len(web_searches),
                 **{key: value for key, value in usage_dict.items()
                    if key in {"input_tokens", "output_tokens", "cached_tokens", "total_tokens"} and value is not None})
    for call in function_calls:
        log.activity("model_tool_call", phase="requested", response_id=response_id,
                     call_id=log.device_correlation(getattr(call, "call_id", None)),
                     tool=call.name if call.name in registered_names else "unknown", **fields)
    for search in web_searches:
        search_fields = {"call_id": log.device_correlation(getattr(search, "id", None)),
                         "response_id": response_id, "tool": "web_search", **fields}
        log.activity("model_builtin_tool", phase="returned",
                     status=getattr(search, "status", None) or "returned", **search_fields)
        action = getattr(search, "action", None)
        log.trace_detail("web_search", payload={
            key: getattr(action, key, None) for key in ("type", "query", "queries", "url", "pattern")
        }, **search_fields)
    # Summaries are provider-authored explanations, never raw/encrypted reasoning.
    summaries = [getattr(summary, "text", "") for item in response_output
                 if getattr(item, "type", None) == "reasoning"
                 for summary in (getattr(item, "summary", None) or [])
                 if getattr(summary, "type", None) == "summary_text"]
    for summary in summaries:
        if summary:
            log.trace_detail("agent_reasoning", payload={"summary": summary},
                             response_id=response_id, **fields)
    if log.trace_enabled() and not any(summaries):
        log.activity("agent_reasoning", status="unavailable", reason="no_summary_returned",
                     response_id=response_id, **fields)
    if final_text:
        log.trace_detail("model_text", payload={"text": final_text},
                         decision="call_tools" if function_calls else "final_text",
                         response_id=response_id, **fields)
    return LLMResponse(
        output=response_output,
        function_calls=function_calls,
        final_text=final_text,
        usage=usage_dict,
        reasoning_count=reasoning_count,
    )


def handle_llm_error(error: Exception, messages: List[Dict]) -> Tuple[str | None, List[Dict]]:
    """Handle LLM-specific errors and return (error_msg, updated_messages).
    
    Args:
        error: The exception that was raised
        messages: Current message history
        
    Returns:
        Tuple of (error_message_for_user, updated_messages)
        - If error_message is None, caller should retry with updated_messages
        - If error_message is a string, caller should abort and return it to user
    """
    error_msg = str(error)
    
    if "context_overflow" in error_msg or "context_length_exceeded" in error_msg:
        log.error("LLM context overflow - reducing message history")
        # Drop complete older turns, keeping function calls with their outputs.
        # A raw last-ten slice can orphan a tool output or duplicate the system
        # message when the history is already short.
        system_count = int(
            bool(messages) and isinstance(messages[0], dict)
            and messages[0].get("role") == "system"
        )
        turn_starts = [
            i for i, message in enumerate(messages)
            if i > system_count and isinstance(message, dict)
            and message.get("role") == "user"
        ]
        if not turn_starts:
            return "That request is too large to process. Please start a shorter request.", messages
        cutoff = max(system_count, len(messages) - 10)
        trim_at = next((i for i in turn_starts if i >= cutoff), turn_starts[-1])
        trimmed_messages = messages[:system_count] + messages[trim_at:]
        return None, trimmed_messages  # Retry with trimmed history
    
    elif "modality_mismatch" in error_msg:
        log.error("LLM modality mismatch - check input format")
        return "I'm having trouble processing that request. Please try again.", messages
    
    elif "No tool output found" in error_msg or "invalid_request_error" in error_msg:
        log.error("LLM tool output mismatch: %s", type(error).__name__)
        # This usually means we didn't send results for all function calls
        return "I'm having issues right now. Please try your request again.", messages
    
    else:
        log.error("LLM API error: %s", type(error).__name__)
        return "I'm having issues right now. Please try again.", messages
