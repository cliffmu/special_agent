"""Core agent structures and stubs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional
import inspect

import voluptuous as vol

_LOGGER = logging.getLogger("custom_components.special_agent")

@dataclass
class ToolSpec:
    """Schema describing a tool."""

    name: str
    description: str
    parameters: vol.Schema
    returns: str | None
    func: Callable[..., Awaitable[Any]]


class Agent:
    """Minimal agent placeholder."""

    def __init__(self) -> None:
        _LOGGER.debug("Agent Init")
        self.tools: Dict[str, ToolSpec] = {}
        self.load_tools()

    def load_tools(self) -> None:
        """Register built-in tools from tool_specs package."""
        try:
            from .tool_specs.search_devices import SPEC as SEARCH_SPEC
            from .tool_specs.build_vector_index import SPEC as BUILD_SPEC
        except Exception as err:  # pragma: no cover - optional tools
            _LOGGER.error("Failed loading tool specs: %s", err)
            return
        self.register_tool(SEARCH_SPEC)
        self.register_tool(BUILD_SPEC)

    def register_tool(self, spec: ToolSpec) -> None:
        _LOGGER.debug("Agent Register Tool")
        self.tools[spec.name] = spec

    async def plan(self, user_input: str, hass: Any | None = None) -> str:
        """Plan and execute a response using available tools."""
        _LOGGER.debug("Agent Plan")
        try:
            return await plan_execute(user_input, list(self.tools.values()), hass=hass)
        except RuntimeError as err:
            _LOGGER.error("Planning failed: %s", err)
            return "I'm not ready to help yet."


def _spec_to_json(spec: ToolSpec) -> Dict:
    """Convert ToolSpec to OpenAI JSON schema."""
    props = {k: {"type": "string"} for k in spec.parameters.schema.keys()}
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": {"type": "object", "properties": props},
        },
    }


async def plan_execute(
    prompt: str,
    tools: List[ToolSpec],
    hass: Optional[Any] = None,
    model: str = "o3-mini",
) -> str:
    """Simple ReAct loop using OpenAI function calling."""
    try:
        import openai
        from openai import AsyncOpenAI  # type: ignore
    except ModuleNotFoundError:  # pragma: no cover - openai optional
        raise RuntimeError("openai package not available")
    except ImportError:
        import openai  # type: ignore  # noqa: F401
        AsyncOpenAI = None  # type: ignore

    tool_json = [_spec_to_json(t) for t in tools]
    system_prompt = (
        "You are Special\u00a0Agent, a smart-home AI.\n" "TOOLS:\n" + str(tool_json)
        + "\nWhen you need to perform an external action, respond with:\n"
        "{\n \"tool_calls\": [{ \"name\": \"<tool>\", \"arguments\": { ... } }] }\n"
        "Otherwise, reply directly to the user."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    depth = 0
    while depth < 3:
        if AsyncOpenAI is not None:
            client = AsyncOpenAI()
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_json,
                tool_choice="auto",
                stream=False,
            )
        else:
            resp = await openai.ChatCompletion.acreate(
                model=model,
                messages=messages,
                tools=tool_json,
                tool_choice="auto",
                stream=False,
            )
        message = resp.choices[0].message
        if message.content:
            _LOGGER.debug("Thought: %s", message.content)
        if not message.tool_calls and not (message.content or "").strip():
            raise RuntimeError("assistant returned empty message")

        if message.tool_calls:
            call = message.tool_calls[0]
            spec_map = {t.name: t for t in tools}
            func = spec_map[call.name].func
            args = spec_map[call.name].parameters(call.arguments)
            _LOGGER.debug("Action: %s %s", call.name, call.arguments)
            if hass is not None and "hass" in inspect.signature(func).parameters:
                result = await func(hass=hass, **args)
            else:
                result = await func(**args)
            _LOGGER.debug("Observation: %s", result)
            messages.append({"role": "assistant", **message})
            messages.append({"role": "tool", "name": call.name, "content": result})
            depth += 1
            continue
        return message.content or ""
    return "Depth limit reached"
