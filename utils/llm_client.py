"""LLM client utilities for API calling, error handling, and metrics extraction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

_LOGGER = logging.getLogger(__package__)

try:  # AsyncOpenAI only in openai>=1.0
    from openai import AsyncOpenAI  # type: ignore
except Exception:  # pragma: no cover - openai optional
    AsyncOpenAI = None  # type: ignore

try:
    from .response_utils import extract_function_calls, extract_final_text
    from . import logging as log
except ImportError:
    from utils.response_utils import extract_function_calls, extract_final_text
    from utils import logging as log

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
    depth: int
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
            flattened_tools.append({
                "type": "function",
                "name": func.get("name"),
                "description": func.get("description"),
                "parameters": func.get("parameters", {}),
            })
        else:
            # Already flattened or different type
            flattened_tools.append(tool)
    
    # Add built-in web_search
    tools_with_search = flattened_tools + [{"type": "web_search"}]
    
    # Call Responses API
    resp = await client.responses.create(
        model=model,
        instructions=instructions,
        input=input_messages,
        tools=tools_with_search,
        tool_choice="auto",
        reasoning={"effort": reasoning_effort},
    )
    
    # Extract response data
    response_output = list(getattr(resp, "output", []))
    function_calls = extract_function_calls(resp)
    final_text = extract_final_text(resp)
    
    # Extract metrics
    reasoning_count = 0
    if hasattr(resp, 'messages') and resp.messages:
        reasoning_count = sum(1 for msg in resp.messages if getattr(msg, 'type', None) == 'reasoning')
    
    usage_dict = {}
    if hasattr(resp, 'usage') and resp.usage:
        usage_dict = {
            "prompt_tokens": getattr(resp.usage, 'prompt_tokens', None),
            "completion_tokens": getattr(resp.usage, 'completion_tokens', None),
            "total_tokens": getattr(resp.usage, 'total_tokens', None),
        }
        log.debug("LLM tokens: prompt=%s, completion=%s, total=%s, reasoning_blocks=%d", 
                 usage_dict.get("prompt_tokens", 'N/A'),
                 usage_dict.get("completion_tokens", 'N/A'),
                 usage_dict.get("total_tokens", 'N/A'),
                 reasoning_count)
    
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
        log.error("LLM tool output mismatch: %s", error)
        # This usually means we didn't send results for all function calls
        return "I'm having issues right now. Please try your request again.", messages
    
    else:
        log.error("LLM API error: %s", error)
        return "I'm having issues right now. Please try again.", messages
