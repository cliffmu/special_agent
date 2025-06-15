"""Core agent structures and stubs (v0.2)."""
from __future__ import annotations

import inspect
import json
import importlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional
import os

import voluptuous as vol

try:
    from .utils import logging as log
except ImportError:  # pragma: no cover - support direct execution
    from utils import logging as log

# ----------  data classes ----------
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: vol.Schema
    returns: str | None
    func: Callable[..., Awaitable[Any]]

# ----------  agent ----------
class Agent:
    """Minimal ReAct‑capable agent."""
    log.debug("Brace {} {}", 1, 2)
    log.debug("Percent %s", "ok")
    log.debug("No placeholders", {"k": 42})


    def __init__(self) -> None:
        self.tools: Dict[str, ToolSpec] = {}
        self.load_tools()

    # —— tool registry ——
    def load_tools(self) -> None:
        """Dynamically import any available tool specs."""
        base = __package__ or ""
        for mod in (
            "tool_specs.build_vector_index",  # always present
            "tool_specs.search_devices",      # optional / future
        ):
            module_name = f"{base}.{mod}" if base else mod
            try:
                module = importlib.import_module(module_name)
                self.register_tool(module.SPEC)
            except Exception as err:  # pragma: no cover
                log.debug("Tool '%s' not loaded: %s", module_name, err)

    def register_tool(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec
        log.info("Tool registered: %s", spec.name)

    # —— entry‑point ——
    async def plan(self, user_input: str, hass: Any | None = None) -> str:
        return await plan_execute(
            user_input,
            list(self.tools.values()),
            hass=hass,
        )

# ----------  helpers ----------
_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}

def _spec_to_json(spec: ToolSpec) -> Dict:
    """Translate Voluptuous schema → JSON schema for OpenAI."""
    props = {}
    for key, validator in spec.parameters.schema.items():
        # crude but effective: map basic python types to JSON‑Schema 'type'
        py_type = getattr(validator, "type", str)
        props[str(key)] = {"type": _JSON_TYPES.get(py_type, "string")}
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": {"type": "object", "properties": props},
        },
    }

# ----------  ReAct loop ----------
async def plan_execute(
    prompt: str,
    tools: List[ToolSpec],
    hass: Optional[Any] = None,
    model: str = "o3-mini",
) -> str:
    log.debug("Brace {} {}", 1, 2)
    log.debug("Percent %s", "ok")
    log.debug("No placeholders", {"k": 42})

    if not os.environ.get("OPENAI_API_KEY"):
        return "Sorry, I'm not ready to help yet."

    try:
        from .utils.openai_client import get_async_client
        client = await get_async_client(hass)
    except Exception as err:  # pragma: no cover - openai optional
        log.error("OpenAI client init failed: %s", err)
        return "Error initializing OpenAI client"

    tool_json = [_spec_to_json(t) for t in tools]
    system_prompt = (
        "You are Special Agent, a smart‑home AI.\nTOOLS:\n"
        f"{json.dumps(tool_json, indent=2)}\n"
        "When an external action is required, reply ONLY with tool_calls."
    )
    log.debug("System_Prompt: %s", system_prompt)
    log.debug("User_Prompt: %s", prompt)
    log.debug("Tools_Provided: %s", tool_json)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    depth = 0
    spec_map = {t.name: t for t in tools}

    while depth < 3:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_json,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        log.debug("Thought: %s", msg.content)
        log.debug("Tools_Selected: %s", msg.tool_calls)

        if msg.tool_calls:
            call = msg.tool_calls[0]
            raw_args = json.loads(call.arguments or "{}")  # ← NEW
            spec = spec_map[call.name]
            args = spec.parameters(raw_args)               # validate
            log.debug("Action: %s %s", call.name, args)

            # execute
            if hass and "hass" in inspect.signature(spec.func).parameters:
                result = await spec.func(hass=hass, **args)
            else:
                result = await spec.func(**args)
            log.debug("Observation: %s", result)

            # feed back
            messages.extend(
                [
                    {"role": "assistant", **msg},
                    {"role": "tool", "name": call.name, "content": str(result)},
                ]
            )
            depth += 1
            continue

        # LLM produced a final answer
        return msg.content or "OK"

    return "Depth‑limit reached (3)."
