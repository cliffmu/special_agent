"""Core agent structures and stubs (v0.2)."""
from __future__ import annotations

import inspect
import json
import importlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional
import os
import time
from datetime import datetime

import voluptuous as vol

try:
    from .utils import logging as log
    from . import DOMAIN
    from .session_store import Session
except ImportError:  # pragma: no cover - support direct execution
    from utils import logging as log
    from __init__ import DOMAIN
    from session_store import Session

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

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.tools: Dict[str, ToolSpec] = {}
        self.config = config or {}
        self._tools_loaded = False

    # —— tool registry ——
    async def load_tools(self, hass: Any | None = None) -> None:
        """Dynamically import any available tool specs."""
        if self._tools_loaded:
            return
        self._tools_loaded = True
        base = __package__ or ""
        
        # Base tools that are always loaded
        base_tools = [
            "tool_specs.build_vector_index",
            "tool_specs.confirm_action",
            "tool_specs.ask_user",
            "tool_specs.search_devices",
            "tool_specs.control_device",
            "tool_specs.search_spotify",
            "tool_specs.get_entity_state",
            "tool_specs.get_entity_history",
            "tool_specs.prepare_voice_response",
            "tool_specs.search_web",
        ]
        
        # Google search integration (commented out - using OpenAI web search instead)
        # To re-enable Google search:
        # 1. Uncomment the block below
        # 2. Add "tool_specs.search_web_google" to base_tools
        # 3. Configure google_api_key and google_cx in config_flow
        #
        # if self.config.get("google_api_key") and self.config.get("google_cx"):
        #     base_tools.append("tool_specs.search_web_google")
        #     log.info("Google search enabled")
        # else:
        #     log.info("Google search disabled - using OpenAI web search")
        
        for mod in base_tools:
            module_name = f"{base}.{mod}" if base else mod
            try:
                # Use executor to avoid blocking the event loop
                if hass:
                    module = await hass.async_add_executor_job(
                        importlib.import_module, module_name
                    )
                else:
                    module = importlib.import_module(module_name)
                
                # Google search credential setup (commented out)
                # if mod == "tool_specs.search_web_google" and hasattr(module, "set_credentials"):
                #     module.set_credentials(
                #         self.config.get("google_api_key"),
                #         self.config.get("google_cx")
                #     )
                
                self.register_tool(module.SPEC)
            except Exception as err:  # pragma: no cover
                log.debug("Tool '%s' not loaded: %s", module_name, err)

    def register_tool(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec
        log.info("Tool registered: %s", spec.name)

    # —— entry‑point ——
    async def plan(
        self,
        user_input: str,
        hass: Any | None = None,
        session_key: tuple[str, str] | None = None,
        model: str = "gpt-5",
        reasoning_effort: str = "medium",
        verbosity: str = "medium",
    ) -> Any:
        # Ensure tools are loaded
        await self.load_tools(hass)
        
        return await plan_execute(
            user_input,
            list(self.tools.values()),
            hass=hass,
            session_key=session_key,
            model=model,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
        )

# ----------  helpers ----------
_JSON_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


def _schema_to_json(value: Any) -> Dict:
    """Recursively convert a Voluptuous schema into JSON Schema."""
    import voluptuous as vol

    if isinstance(value, vol.Schema):
        value = value.schema

    if isinstance(value, dict):
        props: Dict[str, Any] = {}
        required: list[str] = []
        for k, v in value.items():
            name = k.schema if isinstance(k, (vol.Required, vol.Optional)) else k
            if isinstance(k, vol.Required):
                required.append(str(name))
            prop_schema = _schema_to_json(v)
            if isinstance(k, (vol.Optional, vol.Required)) and k.default is not vol.UNDEFINED:
                prop_schema["default"] = k.default()
            props[str(name)] = prop_schema
        result: Dict[str, Any] = {"type": "object", "properties": props}
        if required:
            result["required"] = required
        return result

    if isinstance(value, list):
        item_schema = _schema_to_json(value[0]) if value else {}
        return {"type": "array", "items": item_schema}

    if isinstance(value, vol.Any):
        return {"oneOf": [_schema_to_json(v) for v in value.validators if v is not None]}

    if isinstance(value, vol.All):
        schema: Dict[str, Any] = {}
        for v in value.validators:
            schema.update(_schema_to_json(v))
        return schema

    if isinstance(value, vol.Coerce):
        return _schema_to_json(value.type)

    if isinstance(value, type):
        return {"type": _JSON_TYPES.get(value, "string")}

    return {"type": _JSON_TYPES.get(type(value), "string")}


def _spec_to_json(spec: ToolSpec) -> Dict:
    """Translate Voluptuous schema → JSON schema for OpenAI."""
    params_schema = _schema_to_json(spec.parameters)
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": params_schema,
        },
    }


def _load_session(
    hass: Any | None,
    session_key: tuple[str, str] | None,
    system_prompt: str,
    prompt: str,
) -> tuple[list, Any | None, Dict[str, Any] | None]:
    """Restore a previous session or create a new one."""
    mgr = None
    focus: Dict[str, Any] | None = None
    if hass:
        mgr = hass.data.get(DOMAIN, {}).get("sessions")
        if session_key and mgr:
            session = mgr.get(session_key)
            if session:
                msgs = list(session.messages)
                focus = session.focus
                msgs.append({"role": "user", "content": prompt})
                return msgs, mgr, focus
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ], mgr, focus


def _store_session(
    mgr: Any | None,
    session_key: tuple[str, str] | None,
    messages: list,
    pending: Dict[str, Any] | None,
    focus: Dict[str, Any] | None,
) -> None:
    """Persist session state if a manager is available."""
    if mgr and session_key:
        mgr.set(
            session_key,
            Session(
                messages=messages,
                pending=pending,
                focus=focus,
                device_id=session_key[1],
                updated=time.time(),
            ),
        )


def _clear_session(mgr: Any | None, session_key: tuple[str, str] | None) -> None:
    """Remove a persisted session."""
    if mgr and session_key:
        mgr.pop(session_key)


async def _is_followup_prompt(
    client: Any, history: list[Dict[str, Any]], prompt: str
) -> bool:
    """Ask the LLM if the prompt continues the same conversation."""
    if not history:
        return False
    hist_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in history[-4:] if m.get("content")
    )
    eval_messages = [
        {
            "role": "system",
            "content": (
                "Answer yes or no: Does the NEW prompt continue the same topic as "
                "the prior conversation? Reply only 'yes' or 'no'."
            ),
        },
        {"role": "user", "content": f"{hist_text}\nNEW PROMPT: {prompt}"},
    ]
    try:
        resp = await client.chat.completions.create(
            model="gpt-5", messages=eval_messages
        )
        answer = resp.choices[0].message.content.strip().lower()
        return answer.startswith("yes")
    except Exception as err:  # pragma: no cover - fallback
        log.debug("Follow-up check failed: %s", err)
    return False

# ----------  ReAct loop ----------
async def plan_execute(
    prompt: str,
    tools: List[ToolSpec],
    hass: Optional[Any] = None,
    model: str = "gpt-5",       # Updated to GPT-5
    goals: Optional[List[str]] = None,   # ← new (can be None)
    session_key: tuple[str, str] | None = None,
    reasoning_effort: str = "medium",  # GPT-5 parameter: "minimal", "low", "medium", "high"
    verbosity: str = "medium",         # GPT-5 parameter: "low", "medium", "high"
) -> Any:
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

    # Add current date/time context with timezone
    current_datetime = datetime.now()
    date_str = current_datetime.strftime("%A, %B %d, %Y")
    time_str = current_datetime.strftime("%I:%M %p %Z")
    # If no timezone in strftime, try to get it manually
    if not time_str.strip().endswith(('PST', 'PDT', 'EST', 'EDT', 'MST', 'MDT', 'CST', 'CDT')):
        import time
        tz_name = time.tzname[time.daylight]
        time_str = current_datetime.strftime("%I:%M %p") + f" {tz_name}"
    current_year = current_datetime.year
    
    system_prompt = (
        f"CURRENT DATE & TIME: {date_str} at {time_str}\n"
        f"Current year: {current_year} - When dates are mentioned without a year, assume this year\n"
        "Knowledge cutoff: October 2024.\n"
        "You are Special Agent, a smart‑home AI.\n"
        "When you call any tool you MUST include a line that begins with 'Thought:' summarising why you are calling the tool.\n"
        "CRITICAL: Do NOT write tool_calls as JSON text in your message content - use the actual tool_calls parameter that OpenAI provides.\n"
        f"{goals_block}"
        "You have an index summary of the home with count of entity types for each area:\n"
        f"{json.dumps(area_summary, indent=2)[:4000]}\n"  # keep ≤4 KB to protect context
        "Use this device list to:\n"
        "- Decide if search_devices will help (if user asks about lights and you see 'light' listed, search for them!)\n"
        "- Choose the right area and domain for search_devices (k = count shown + a few extra)\n"
        "- Avoid searching for device types not listed (tell user they're not available)\n"
        "WEB SEARCH TOOL:\n"
        "- search_web uses OpenAI with real-time sports scores, weather, and news\n"
        "- It returns an interpreted 'answer' - the search is already done for you\n"
        "- Use it when you need current info after Oct 2024 or time-sensitive data\n"
        "- For sports scores, include specific dates: 'Yankees score September 28' not 'yesterday'\n"
        "WEATHER QUERIES:\n"
        "- For weather, ALWAYS check Home Assistant weather entities FIRST (search_devices domain=weather)\n"
        "- The 'temperature' attribute from weather entities IS the current temperature - use it directly\n"
        "- Only use search_web for weather if no HA weather entities are available\n"
        "TOOLS:\n"
        f"{json.dumps(tool_json, indent=2)}\n"
        "Example tool_calls JSON: [\n"
        "  {\"type\": \"function\", \"function\": {\n"
        "    \"name\": \"confirm_action\",\n"
        "    \"arguments\": {\"action\": \"turn off\", \"targets\": [\"light.kitchen\"]}\n"
        "  }}\n"
        "]\n"
        f"MODEL: {model} | Reasoning: {reasoning_effort} | Verbosity: {verbosity}\n"
        "After every tool result you must:\n"
        "• reflect on whether the observation fully answers the user’s goal and, if goals "
        "are listed, mark completed goals as (done);\n"
        "• include a 0‑100 % confidence score in the form 'Confidence: NN%';\n"
        "• if NOT satisfied, brainstorm ONE improved call (re‑phrase query, bigger k, etc.) "
        "and invoke it; do this at most 2 times per user request;\n"
        "• never repeat an identical call already tried;\n"
        "• once satisfied, call prepare_voice_response with your answer to format it for voice output.\n"
        "When an external action is required, you MAY include both a Thought paragraph and "
        "tool_calls in the same message."
        "\nRULES:\n"
        "- For FINAL ANSWERS, always use prepare_voice_response (do NOT use it for confirm_action/ask_user - they format themselves)\n"
        "- NEVER suggest checking external services (MLB, ESPN, etc) - use only the tools you have available\n"
        "- If you can't find info with search_web, just say you don't have access to that information\n"
        "- When you call confirm_action you MUST include a `question` field containing the exact sentence to speak"
    )

    log.debug("System_Prompt: %s", system_prompt)
    log.debug("User_Prompt: %s", prompt)
    log.debug("Tools_Provided: %s", tool_json)     # ← your tweak #2

    # ---- state ----
    messages, mgr, focus = _load_session(hass, session_key, system_prompt, prompt)
    if focus:
        try:
            follow = await _is_followup_prompt(client, messages[:-1], prompt)
        except Exception as err:  # pragma: no cover
            log.debug("Follow-up check error: %s", err)
            follow = False
        if not follow:
            _clear_session(mgr, session_key)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            focus = None
    tried_calls: set[tuple[str, str]] = set()
    retry_budget = 2
    depth = 0
    max_depth = 7
    spec_map = {t.name: t for t in tools}
    needs_voice_response = True  # Track if we need to format final response

    # ---- helper: summarise observation ----
    def _summarise(result: Any) -> str:
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
        except Exception as err:           # pragma: no cover
            return f"(summary error: {err})"

    # ---- main loop ----
    while depth < max_depth:
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_json,
                tool_choice="auto",
                reasoning_effort=reasoning_effort,  # GPT-5 reasoning depth control
                verbosity=verbosity,                # GPT-5 response detail level
                # temperature=0.4,
            )
            msg = resp.choices[0].message
        except Exception as err:
            # Handle GPT-5 specific errors
            error_msg = str(err)
            if "context_overflow" in error_msg:
                log.error("GPT-5 context overflow - reducing message history")
                # Keep system message and last 5 user/assistant messages
                messages = messages[:1] + messages[-10:]
                continue
            elif "modality_mismatch" in error_msg:
                log.error("GPT-5 modality mismatch - check input format")
                return "Error: Input format not compatible with GPT-5"
            else:
                log.error("GPT-5 API error: %s", err)
                return f"Error calling GPT-5: {err}"
        log.debug("AI_Response_Content: %s", msg.content)      # ← your tweak #3
        if msg.tool_calls:
            log.debug(
                "AI_Response_Tools_Selected: %s",
                [(tc.function.name, tc.function.arguments) for tc in msg.tool_calls],
            )
        log.debug("AI_Response_Full: %s", msg)

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
                    {
                        "role": "assistant",
                        "content": (
                            "Thought: Duplicate of previous attempt; refining… Confidence: 30%"
                        ),
                    },
                )
                depth += 1
                retry_budget -= 1
                if retry_budget < 0:
                    # Force a voice response before giving up
                    messages.append({
                        "role": "assistant",
                        "content": (
                            "Thought: Maximum retries reached. I need to prepare a voice response "
                            "to explain the situation to the user. Confidence: 100%"
                        )
                    })
                    depth += 1
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
            try:
                if hass and "hass" in inspect.signature(spec.func).parameters:
                    call_args = {"hass": hass, **args}
                    log.debug("Tool_Input[%s]: %s", call_name, call_args)
                    result = await spec.func(**call_args)
                else:
                    call_args = args
                    log.debug("Tool_Input[%s]: %s", call_name, call_args)
                    result = await spec.func(**call_args)
                log.debug("Tool_Result[%s]: %s", call_name, result)
            except Exception as err:
                log.error("Tool execution failed: %s", err)
                messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            f"Thought: Execution of {call_name} failed (`{err}`). "
                            "Please adjust the parameters and try again. Confidence: 20%"
                        ),
                    }
                )
                depth += 1
                retry_budget -= 1
                if retry_budget >= 0:
                    continue
                _clear_session(mgr, session_key)
                return f"Error: {err}"

            if call_name == "control_device":
                focus = {
                    "targets": args["data"].get("entity_id", []),
                    "action": args["service"],
                }
            elif isinstance(result, dict) and result.get("focus"):
                focus = result["focus"]

            # feed back – store SUMMARISED observation
            content_summary = _summarise(result)
            msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else msg.dict()
            messages.extend(
                [
                    {"role": "assistant", **msg_dict},
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call_name,
                        "content": content_summary,
                    },
                ]
            )
            if isinstance(result, dict) and "speak" in result:
                _store_session(mgr, session_key, messages, result.get("pending"), focus)
                return {"prompt_payload": result, "messages": messages}
            depth += 1
            if retry_budget > 0:
                retry_budget -= 1
            continue

        # ---------- final answer ----------
        log.debug("Final Message: %s", messages)
        _store_session(mgr, session_key, messages, None, focus)
        return msg.content or "OK"

    return "Depth‑limit reached."

