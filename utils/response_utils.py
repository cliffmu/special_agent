"""Utilities for parsing and formatting Responses API output."""

from __future__ import annotations

from typing import Any
import json


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

