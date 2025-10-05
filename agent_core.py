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
    from .utils.session_helpers import load_session, store_session, clear_session
    from .utils.response_utils import extract_function_calls, extract_final_text, summarize_result
    from .utils import performance
except ImportError:  # pragma: no cover - support direct execution
    from utils import logging as log
    from utils.session_helpers import load_session, store_session, clear_session
    from utils.response_utils import extract_function_calls, extract_final_text, summarize_result
    from utils import performance
    DOMAIN = "special_agent"

# ----------  data classes ----------
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema (OpenAI format)
    returns: str | None
    func: Callable[..., Awaitable[Any]]
    validate: Callable[[dict], dict] | None = None  # Optional validation function
    can_run_parallel: bool = True  # Can this tool run concurrently with others?

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
            "tool_specs.ask_user",
            "tool_specs.search_devices",
            "tool_specs.control_device",
            "tool_specs.search_spotify",
            "tool_specs.get_entity_state",
            "tool_specs.get_entity_history",
            "tool_specs.prepare_voice_response",
            # search_web not loaded - using OpenAI's built-in web_search instead
        ]
        
        # Conditionally load confirm_action based on config
        if self.config.get("require_confirmation", True):
            base_tools.insert(1, "tool_specs.confirm_action")
            log.info("Confirmation enabled - confirm_action tool loaded")
        else:
            log.info("Confirmation disabled - confirm_action tool not loaded")
        
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
        model: str | None = None,
    ) -> Any:
        # Ensure tools are loaded
        await self.load_tools(hass)
        
        # Get model and reasoning_effort from config
        model = model or self.config.get("agent_model", "gpt-5")
        reasoning_effort = self.config.get("reasoning_effort", "low")
        require_confirmation = self.config.get("require_confirmation", True)
        session_timeout_minutes = self.config.get("session_timeout_minutes", 5)
        
        return await plan_execute(
            user_input,
            list(self.tools.values()),
            hass=hass,
            session_key=session_key,
            model=model,
            reasoning_effort=reasoning_effort,
            require_confirmation=require_confirmation,
            session_timeout_minutes=session_timeout_minutes,
        )

# ----------  helpers ----------
def _spec_to_json(spec: ToolSpec) -> Dict:
    """Convert ToolSpec to OpenAI Responses API format."""
    return {
        "type": "function",
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.parameters,  # Already in JSON schema format
    }


# ----------  ReAct loop ----------
async def plan_execute(
    prompt: str,
    tools: List[ToolSpec],
    hass: Optional[Any] = None,
    model: str = "gpt-5",
    goals: Optional[List[str]] = None,
    session_key: tuple[str, str] | None = None,
    reasoning_effort: str = "low",  # Responses API: "minimal", "low", "medium", "high"
    require_confirmation: bool = True,
    session_timeout_minutes: int = 5,
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
    
    # Build confirmation-specific instructions
    confirmation_instructions = ""
    parallel_execution_note = ""
    if require_confirmation:
        confirmation_instructions = (
            "CONFIRMATION REQUIRED:\n"
            "- Before using control_device to change device states, you MUST call confirm_action first\n"
            "- When you call confirm_action you MUST include a 'question' field with the exact sentence to speak\n"
            "- Example: 'Do you want me to turn off the kitchen lights?'\n"
        )
        parallel_execution_note = "- NEVER call confirm_action, ask_user, or prepare_voice_response in parallel with other tools - they are final-step tools\n"
    else:
        confirmation_instructions = (
            "CONFIRMATION DISABLED:\n"
            "- You can use control_device directly without confirmation\n"
            "- After executing control_device, call prepare_voice_response to report the result\n"
        )
        parallel_execution_note = "- NEVER call ask_user or prepare_voice_response in parallel with other tools - they are final-step tools\n"
    
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
        "Check this device list before using search_devices - if a type isn't listed, tell user it's not available.\n"
        "WEB SEARCH:\n"
        "- Built-in web_search provides real-time sports, weather, news when you need current info after Oct 2024\n"
        "TOOLS:\n"
        f"{json.dumps(tool_json, indent=2)}\n"
        f"{confirmation_instructions}"
        "PARALLEL EXECUTION:\n"
        "- When multiple tools are independent (e.g., search_devices + search_spotify), call them in parallel in ONE response\n"
        "- Example: To play music, call BOTH search_devices AND search_spotify together, not sequentially\n"
        f"{parallel_execution_note}"
        "- Only call tools sequentially if one depends on the output of another\n"
        "SPOTIFY SEARCH TIPS:\n"
        "- Start with ONE search query that best matches the user's request\n"
        "- If search_spotify returns null, try: simpler queries, genre names, or artist radio (e.g., 'Tycho Radio')\n"
        "- Only use parallel searches if the first attempt fails and you want to try different variations\n"
        "- After 2-3 failed attempts, ask user for a Spotify link or different music preference\n"
        f"MODEL: {model} | Reasoning: {reasoning_effort} | Confirmation: {'Enabled' if require_confirmation else 'Disabled'}\n"
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
        "- NEVER suggest checking external services - use only the tools you have available\n"
        "- If you can't find info, just say you don't have access to that information"
    )

    log.debug("System_Prompt: %s", system_prompt)
    log.debug("User_Prompt: %s", prompt)
    log.debug("Tools_Provided: %s", tool_json)
    log.debug("Config: model=%s, reasoning=%s, require_confirmation=%s, timeout=%d min", 
              model, reasoning_effort, require_confirmation, session_timeout_minutes)
    log.debug("Loaded tools: %s", [spec.name for spec in tools])

    # ---- state ----
    async with performance.track_operation("load_session"):
        messages, mgr, focus, pending = load_session(
            hass, session_key, system_prompt, prompt, session_timeout_minutes
        )
    log.debug("Session loaded: messages=%d, focus=%s, pending=%s", len(messages), focus, pending)
    
    # Session timeout naturally handles topic changes - no expensive LLM check needed
    # If user starts new topic after timeout, session will be auto-cleared
    # If within timeout and same conversation_id, trust it's a continuation
    
    # If there's a pending confirmation, add it to context
    if pending:
        log.debug("Pending confirmation exists: %s", pending)
    tried_calls: set[tuple[str, str]] = set()
    retry_budget = 2
    depth = 0
    max_depth = 7
    spec_map = {t.name: t for t in tools}

    # ---- main loop ----
    while depth < max_depth:
        try:
            # Add built-in web_search to tools
            tools_with_search = tool_json + [{"type": "web_search"}]
            
            # Responses API uses 'instructions' instead of system message
            # Extract system from messages if present
            instructions = None
            input_messages = messages
            if messages and messages[0].get("role") == "system":
                instructions = messages[0].get("content")
                input_messages = messages[1:]
            
            # Use Responses API for GPT-5 with web search support
            async with performance.track_operation(
                f"llm_call_{depth+1}",
                metadata={"model": model, "reasoning": reasoning_effort, "depth": depth}
            ):
                resp = await client.responses.create(
                    model=model,
                    instructions=instructions,
                    input=input_messages,
                    tools=tools_with_search,
                    tool_choice="auto",
                    reasoning={"effort": reasoning_effort},
                )
            
            # Extract function calls and final text from response
            function_calls = extract_function_calls(resp)
            final_text = extract_final_text(resp)
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
                return "I'm having trouble processing that request. Please try again."
            elif "No tool output found" in error_msg or "invalid_request_error" in error_msg:
                log.error("GPT-5 tool output mismatch: %s", err)
                # This usually means we didn't send results for all function calls
                # Clear messages and restart conversation
                clear_session(mgr, session_key)
                return "I'm having issues right now. Please try your request again."
            else:
                log.error("GPT-5 API error: %s", err)
                return "I'm having issues right now. Please try again."
        log.debug("AI_Response_Text: %s", final_text)
        if function_calls:
            log.debug(
                "AI_Response_Function_Calls: %s",
                [(fc.name, fc.arguments) for fc in function_calls],
            )
        
        # Add entire response output to messages (Responses API native format)
        # OpenAI handles all item types (message, function_call, reasoning, web_search_call)
        messages.extend(resp.output)

        # ---------- tool branch ----------
        if function_calls:
            import asyncio
            
            # Phase 1: Validate all calls and check parallel execution rules
            tasks_to_run = []  # List of (call, spec, args) tuples
            validation_errors = []  # Track validation errors
            
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
                messages.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": error_msg
                })
            
            # Phase 2: Execute all valid calls in parallel
            async def execute_tool(call, spec, args):
                """Execute a single tool and return (call, result_or_error, is_error)"""
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
                    parallel_id = str(id(tasks_to_run))
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
            
            for call, spec, args, result, is_error in results:
                call_name = spec.name
                
                if is_error:
                    all_results_success = False
                    messages.append({
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": result  # Already formatted as error string
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
                messages.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result_str
                })
                
                # Check if any result has a prompt response
                if isinstance(result, dict) and "speak" in result:
                    if has_prompt_response:
                        # Multiple tools returned "speak" - this shouldn't happen!
                        log.warning(
                            "Multiple tools returned 'speak' in parallel: %s and %s",
                            prompt_result.get("kind", "unknown"),
                            result.get("kind", "unknown")
                        )
                        # Prioritize: confirm > ask_user > response
                        result_kind = result.get("kind", "")
                        current_kind = prompt_result.get("kind", "")
                        if result_kind == "confirm" or (result_kind == "clarify" and current_kind == "response"):
                            prompt_result = result
                    else:
                        has_prompt_response = True
                        prompt_result = result
            
            # If we got a prompt response, return it
            if has_prompt_response:
                store_session(mgr, session_key, messages, prompt_result.get("pending"), focus)
                return {"prompt_payload": prompt_result, "messages": messages}
            
            # Continue the loop
            depth += 1
            if not all_results_success and retry_budget > 0:
                retry_budget -= 1
            elif retry_budget > 0:
                retry_budget -= 1
            
            if retry_budget < 0:
                # Give up and ask for final response
                messages.append({
                    "role": "user",
                    "content": "You've tried multiple times. Please provide a final answer using prepare_voice_response."
                })
                depth += 1
            continue

        # ---------- final answer ----------
        log.debug("Final Messages: %s", messages)
        async with performance.track_operation("store_session"):
            store_session(mgr, session_key, messages, None, focus)
        # Write metrics after each request completes
        if performance.is_enabled():
            performance.write_csv()
        return final_text or "OK"

    return "Depth‑limit reached."

