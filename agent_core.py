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
        # crudely map Python/voluptuous validators to JSON Schema types
        py_type = None
        if isinstance(validator, type):
            py_type = validator
        else:
            py_type = getattr(validator, "type", None)
        if py_type is None:
            py_type = str
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
    model: str = "o4-mini",          # ← your tweak #1
    goals: Optional[List[str]] = None,   # ← new (can be None)
) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        return "Sorry, I'm not ready to help yet."

    # ---- OpenAI client ----
    try:
        from .utils.openai_client import get_async_client
        client = await get_async_client(hass)
    except Exception as err:                       # pragma: no cover
        log.error("OpenAI client init failed: %s", err)
        return "Error initializing OpenAI client"

    # ---- build system prompt ----
    from .utils.vector_index import async_load_vector_meta
    meta = await async_load_vector_meta(hass=hass)
    area_summary = meta.get("area_summary", {}) if isinstance(meta, dict) else {}
    tool_json = [_spec_to_json(t) for t in tools]
    goals_block = ""
    if goals:
        goals_fmt = "\n".join(f"{idx+1}. {g}" for idx, g in enumerate(goals))
        goals_block = f"\nGOALS:\n{goals_fmt}\n"

    system_prompt = (
        "You are Special Agent, a smart‑home AI.\n"
        "You MUST include a Thought paragraph every time you send tool_calls; if omitted I will assume the message failed.\n"
        f"{goals_block}"
        "You have an index summary of the home with count of entity types for each area:\n"
        f"{json.dumps(area_summary, indent=2)[:4000]}\n"  # keep ≤4 KB to protect context
        "If you plan to call search_devices, use this data to choose the most likely area and domain names, and pick k slightly larger than the expected count."
        "TOOLS:\n"
        f"{json.dumps(tool_json, indent=2)}\n"
        "After every tool result you must:\n"
        "• reflect on whether the observation fully answers the user’s goal and, if goals "
        "are listed, mark completed goals as (done);\n"
        "• include a 0‑100 % confidence score in the form 'Confidence: NN%';\n"
        "• if NOT satisfied, brainstorm ONE improved call (re‑phrase query, bigger k, etc.) "
        "and invoke it; do this at most 2 times per user request;\n"
        "• never repeat an identical call already tried;\n"
        "• once satisfied, talk to the user in clear, friendly language designed to be spoken "
        "aloud to concisely convey information without symbols (no entity IDs unless they "
        "explicitly asked for them) and stop.\n"
        "When an external action is required, you MAY include both a Thought paragraph and "
        "tool_calls in the same message."
    )

    log.debug("System_Prompt: %s", system_prompt)
    log.debug("User_Prompt: %s", prompt)
    log.debug("Tools_Provided: %s", tool_json)     # ← your tweak #2

    # ---- state ----
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    tried_calls: set[tuple[str, str]] = set()
    retry_budget = 2
    depth = 0
    max_depth = 7
    spec_map = {t.name: t for t in tools}

    # ---- helper: summarise observation ----
    def _summarise(result: Any) -> str:
        try:
            if isinstance(result, list):
                head = ", ".join(map(str, result[:5]))
                return f"{len(result)} items: {head}{' …' if len(result) > 5 else ''}"
            if isinstance(result, dict):
                keys = list(result.keys())[:5]
                return f"dict with {len(result)} keys: {', '.join(keys)}{' …' if len(result) > 5 else ''}"
            txt = str(result)
            return txt if len(txt) <= 200 else txt[:200] + " …"
        except Exception as err:           # pragma: no cover
            return f"(summary error: {err})"

    # ---- main loop ----
    while depth < max_depth:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_json,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        log.debug("Thought: %s", msg.content)      # ← your tweak #3
        log.debug("Tools_Selected: %s", msg.tool_calls)   # ← your tweak #3

        # ---------- tool branch ----------
        if msg.tool_calls:
            call = msg.tool_calls[0]
            call_name = call.function.name
            raw_json  = call.function.arguments or "{}"
            canonical = (call_name, json.dumps(json.loads(raw_json), sort_keys=True))

            # duplicate guard
            if canonical in tried_calls:
                log.debug("Duplicate call blocked: %s", canonical)
                messages.append(
                    {"role": "assistant",
                     "content": "Thought: Duplicate of previous attempt; refining… Confidence: 30%"}
                )
                continue
            tried_calls.add(canonical)

            # validate & execute
            try:
                spec = spec_map[call_name]
                args = spec.parameters(json.loads(raw_json))   # validate
            except Exception as err:         # ← catches MultipleInvalid and others
                log.debug("Validation error: %s", err)
                messages.append({
                    "role": "assistant",
                    "content": (
                        "Thought: Tool arguments were invalid "
                        f"(`{err}`); please supply the missing or "
                        "malformed fields and try again. Confidence: 25%"
                    )
                })
                # do NOT decrement retry_budget or depth; let the model fix itself
                continue
            # ---------------------------------------------
            log.debug("Action: %s %s", call_name, args)
            if hass and "hass" in inspect.signature(spec.func).parameters:
                result = await spec.func(hass=hass, **args)
            else:
                result = await spec.func(**args)
            log.debug("Observation: %s", result)

            # feed back – store SUMMARISED observation
            observation_summary = _summarise(result)
            msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else msg.dict()
            messages.extend(
                [
                    {"role": "assistant", **msg_dict},
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call_name,
                        "content": observation_summary,
                    },
                ]
            )
            depth += 1
            if retry_budget > 0:
                retry_budget -= 1
            continue

        # ---------- final answer ----------
        return msg.content or "OK"

    return "Depth‑limit reached."

