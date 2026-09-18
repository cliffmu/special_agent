"""Core agent structures and stubs (v0.2) - SIMPLIFIED."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

# Scene Memory configuration (hardcoded for easy tweaking)
SCENE_MEMORY_CONFIG = {
    "retrieval_k": 1,                          # Default scenes to retrieve
    "session_window_seconds": 360,             # Episode grouping window (6 min)
    "post_condition_timeout_ms": 4000,         # Per-step verification timeout
    "session_close_idle_seconds": 120,         # Episode flush after idle (2 min)
}

try:
    from .utils import logging as log
    from .utils.constants import DEFAULT_AGENT_MODEL, normalize_reasoning_effort
    from . import DOMAIN
    from .utils.session_helpers import load_session, store_session, clear_session, generate_message_id
    from .utils.response_utils import validate_and_execute_tools, trace_agent_response
    from .utils.llm_client import get_async_client, call_llm, handle_llm_error
    from .utils.prompt_builder import build_system_prompt
    from .utils import performance
    from .utils import tool_registry as _tool_registry
except ImportError:  # pragma: no cover - support direct execution
    from utils import logging as log
    from utils.constants import DEFAULT_AGENT_MODEL, normalize_reasoning_effort
    from utils.session_helpers import load_session, store_session, clear_session, generate_message_id
    from utils.response_utils import validate_and_execute_tools, trace_agent_response
    from utils.llm_client import get_async_client, call_llm, handle_llm_error
    from utils.prompt_builder import build_system_prompt
    from utils import performance
    from utils import tool_registry as _tool_registry
    DOMAIN = "special_agent"

# Import tool registry functions for convenience
ToolSpec = _tool_registry.ToolSpec
register_tool_spec = _tool_registry.register_tool_spec
get_registered_tool_specs = _tool_registry.get_registered_tool_specs
get_sequence_safe_tool_specs = _tool_registry.get_sequence_safe_tool_specs
spec_to_json = _tool_registry.spec_to_json
load_all_tools = _tool_registry.load_all_tools

# ----------  agent ----------
class Agent:
    """Minimal ReAct‑capable agent."""

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.tools: Dict[str, ToolSpec] = {}
        self.config = dict(config or {})
        self._tools_loaded = False
        self._tools_lock = asyncio.Lock()

    # —— tool registry ——
    async def load_tools(self, hass: Any | None = None) -> None:
        """Dynamically import all available tool specs."""
        if self._tools_loaded:
            return
        
        # Simultaneous satellite requests share one completed tool load.
        async with self._tools_lock:
            if not self._tools_loaded:
                self.tools = await load_all_tools(hass, self.config)
                self._tools_loaded = True

    def register_tool(self, spec: ToolSpec) -> None:
        """Register a single tool."""
        self.tools[spec.name] = spec
        register_tool_spec(spec)
        log.info("Tool registered: %s", spec.name)

    # —— entry‑point ——
    async def plan(
        self,
        user_input: str,
        hass: Any | None = None,
        session_key: tuple[str, str] | None = None,
        model: str | None = None,
        *,
        reasoning_effort: str | None = None,
        fast_mode: bool | None = None,
    ) -> Any:
        # Ensure tools are loaded
        await self.load_tools(hass)
        
        # Get model and reasoning_effort from config
        model = model or self.config.get("agent_model", DEFAULT_AGENT_MODEL)
        reasoning_effort = reasoning_effort if reasoning_effort is not None else self.config.get("reasoning_effort", "low")
        fast_mode = fast_mode if fast_mode is not None else self.config.get("fast_mode", False)
        require_confirmation = self.config.get("require_confirmation", True)
        session_timeout_minutes = self.config.get("session_timeout_minutes", 5)
        
        return await plan_execute(
            user_input,
            list(self.tools.values()),
            hass=hass,
            session_key=session_key,
            model=model,
            reasoning_effort=reasoning_effort,
            fast_mode=fast_mode,
            require_confirmation=require_confirmation,
            session_timeout_minutes=session_timeout_minutes,
        )

# ----------  helpers ----------
# (Schema and spec conversion functions moved to utils/tool_registry.py)
# (Message conversion functions moved to utils/response_utils.py)


# ----------  ReAct loop ----------
async def plan_execute(
    prompt: str,
    tools: List[ToolSpec],
    hass: Optional[Any] = None,
    model: str = DEFAULT_AGENT_MODEL,
    goals: Optional[List[str]] = None,
    session_key: tuple[str, str] | None = None,
    reasoning_effort: str = "low",
    require_confirmation: bool = True,
    session_timeout_minutes: int = 5,
    fast_mode: bool = False,
) -> Any:
    """Execute ReAct agent loop with clean orchestration."""
    reasoning_effort = normalize_reasoning_effort(model, reasoning_effort)
    log.activity("agent_loop", phase="started", source="agent_loop", model=model,
                 effort=reasoning_effort, tool_count=len(tools))
    log.trace_detail("agent_request", payload={"text": prompt}, source="agent_loop")
    
    # Validate API key
    if not os.environ.get("OPENAI_API_KEY"):
        return trace_agent_response(
            "Sorry, I'm not ready to help yet. Please add Open AI API key to the integration configuration.",
            status="error", reason="missing_api_key")

    # Setup: Get client and build system prompt
    try:
        client = await get_async_client(hass)
        system_prompt = await build_system_prompt(
            tools, hass, session_key, model, reasoning_effort, require_confirmation, goals
        )
    except asyncio.CancelledError:
        log.activity("agent_loop", phase="finished", source="agent_loop", status="cancelled", reason="setup_cancelled")
        raise
    except Exception as err:
        log.error("Setup failed: %s", type(err).__name__)
        return trace_agent_response("Error initializing agent", status="error", reason="setup_failed")

    # Load session (tracks internally)
    messages, mgr, focus, pending = load_session(
        hass, session_key, system_prompt, prompt, session_timeout_minutes
    )
    
    # Set session ID for performance tracking
    if performance.is_enabled() and session_key:
        session_id = f"{session_key[0]}|{session_key[1]}"
        performance.set_session_id(session_id)
        
        # Track followup status
        is_followup = len(messages) > 1
        request_id = performance.get_current_request_id()
        if request_id:
            for record in performance.get_records():
                if record.request_id == request_id and record.operation == "user_request":
                    record.metadata["is_followup"] = is_followup
                    record.metadata["session_msg_count"] = len(messages)
                    break
    
    log.activity("agent_loop", phase="session_loaded", source="agent_loop", message_count=len(messages),
                 decision="resume_pending" if pending else "continue_session" if len(messages) > 2 else "new_request")
    
    # Initialize loop state
    tried_calls: set[tuple[str, str]] = set()
    depth, max_depth = 0, 10
    retry_budget = 2
    error_retries_remaining = 2
    spec_map = {t.name: t for t in tools}
    tool_json = [spec_to_json(t) for t in tools]

    # Main ReAct loop
    while depth < max_depth:
        # Call LLM with performance tracking
        try:
            async with performance.track_llm_call(model, reasoning_effort, depth, len(messages)) as tracker:
                response = await call_llm(client, messages, tool_json, model, reasoning_effort, depth, fast_mode=fast_mode)
                tracker.set_response(response)
        except asyncio.CancelledError:
            log.activity("agent_loop", phase="finished", source="agent_loop", status="cancelled",
                         reason="model_cancelled", iteration=depth + 1)
            raise
        except Exception as err:
            # Handle LLM errors
            error_msg, messages = handle_llm_error(err, messages)
            if error_msg:  # Unrecoverable error
                store_session(mgr, session_key, messages, pending, focus)
                return trace_agent_response(error_msg, status="error", reason="model_failed", iteration=depth + 1)
            if error_retries_remaining == 0:
                log.warning("LLM error retry limit reached")
                store_session(mgr, session_key, messages, pending, focus)
                return trace_agent_response(
                    "I'm having trouble with this conversation. Please start a new request.",
                    status="error", reason="model_retries_exhausted", iteration=depth + 1)
            error_retries_remaining -= 1
            log.activity("agent_loop", phase="retrying", source="agent_loop", iteration=depth + 1,
                         remaining=error_retries_remaining, reason="model_error", message_count=len(messages))
            continue  # Retry with updated messages
        
        # Add response to history
        messages.extend(response.output)
        
        # Check for final answer (no tool calls)
        if response.final_text and not response.function_calls:
            # Skip intermediate acknowledgments
            cleaned = (response.final_text or "").strip().lower()
            intermediate_ack = cleaned in {"yes", "yeah", "yep", "yup", "sure", "no", "nope"}
            if not intermediate_ack:
                async with performance.track_operation("store_session"):
                    store_session(mgr, session_key, messages, pending, focus)
                return trace_agent_response(response.final_text or "OK", reason="final_text", iteration=depth + 1)
        
        # Execute tools if present
        if response.function_calls:
            try:
                tool_results = await validate_and_execute_tools(
                    response.function_calls, spec_map, tried_calls, hass, iteration=depth + 1
                )
            except (Exception, asyncio.CancelledError) as error:
                log.activity("agent_loop", phase="finished", source="agent_loop", iteration=depth + 1,
                             status="cancelled" if isinstance(error, asyncio.CancelledError) else "error",
                             reason="tool_execution_aborted", error_type=type(error).__name__)
                raise
            
            # Add tool results to message history
            messages.extend(tool_results.messages)
            focus = tool_results.focus or focus
            
            # Check for prompt response (confirm/ask)
            if tool_results.prompt_response:
                store_session(mgr, session_key, messages, tool_results.prompt_response, focus)
                return trace_agent_response(
                    {"prompt_payload": tool_results.prompt_response, "messages": messages},
                    reason="tool_response", iteration=depth + 1)
            
            # Check if all tools failed
            if tool_results.all_failed:
                store_session(mgr, session_key, messages, pending, focus)
                return trace_agent_response(tool_results.error_message, status="error",
                                            reason="all_tools_failed", iteration=depth + 1)

        log.activity("agent_loop", phase="continuing", source="agent_loop", iteration=depth + 1,
                     reason="tool_results_ready" if response.function_calls else "no_final_answer",
                     message_count=len(messages))
        
        # Continue loop
        depth += 1
        if retry_budget > 0:
            retry_budget -= 1
        
        # Request final answer if out of retries
        if retry_budget < 0:
            messages.append({
                "role": "user",
                "content": "You've tried multiple times. Please provide a final answer using prepare_voice_response.",
                "id": generate_message_id()
            })
            depth += 1
        
        # Check depth limit
        if depth >= max_depth:
            log.warning("Depth limit reached")
            store_session(mgr, session_key, messages, pending, focus)
            return trace_agent_response("Depth limit reached. Please try again.", status="error",
                                        reason="depth_limit", iteration=depth)
    
    # Fallback if loop exits without returning
    log.error("Agent loop exited without response")
    return trace_agent_response("I apologize, but I wasn't able to complete your request.",
                                status="error", reason="no_response", iteration=depth)
