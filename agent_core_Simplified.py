"""Core agent structures and stubs (v0.2) - SIMPLIFIED."""
from __future__ import annotations

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
    from . import DOMAIN
    from .utils.session_helpers import load_session, store_session, clear_session, generate_message_id
    from .utils.response_utils import validate_and_execute_tools
    from .utils.llm_client import get_async_client, call_llm, handle_llm_error
    from .utils.prompt_builder import build_system_prompt
    from .utils import performance
    from .utils import tool_registry as _tool_registry
except ImportError:  # pragma: no cover - support direct execution
    from utils import logging as log
    from utils.session_helpers import load_session, store_session, clear_session, generate_message_id
    from utils.response_utils import validate_and_execute_tools
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
        self.config = config or {}
        self._tools_loaded = False

    # —— tool registry ——
    async def load_tools(self, hass: Any | None = None) -> None:
        """Dynamically import all available tool specs."""
        if self._tools_loaded:
            return
        
        # Use centralized tool loading from tool_registry
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
# (Schema and spec conversion functions moved to utils/tool_registry.py)
# (Message conversion functions moved to utils/response_utils.py)


# ----------  ReAct loop ----------
async def plan_execute(
    prompt: str,
    tools: List[ToolSpec],
    hass: Optional[Any] = None,
    model: str = "gpt-5",
    goals: Optional[List[str]] = None,
    session_key: tuple[str, str] | None = None,
    reasoning_effort: str = "low",
    require_confirmation: bool = True,
    session_timeout_minutes: int = 5,
) -> Any:
    """Execute ReAct agent loop with clean orchestration."""
    
    # Validate API key
    if not os.environ.get("OPENAI_API_KEY"):
        return "Sorry, I'm not ready to help yet."

    # Setup: Get client and build system prompt
    try:
        client = await get_async_client(hass)
        system_prompt = await build_system_prompt(
            tools, hass, session_key, model, reasoning_effort, require_confirmation, goals
        )
    except Exception as err:
        log.error("Setup failed: %s", err)
        return "Error initializing agent"

    # Load session
    async with performance.track_operation("load_session"):
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
    
    log.debug("Session loaded: messages=%d, focus=%s, pending=%s", len(messages), focus, pending)
    if pending:
        log.debug("Pending confirmation exists: %s", pending)
    
    # Initialize loop state
    tried_calls: set[tuple[str, str]] = set()
    depth, max_depth = 0, 10
    retry_budget = 2
    spec_map = {t.name: t for t in tools}
    tool_json = [spec_to_json(t) for t in tools]

    # Main ReAct loop
    try:
        while depth < max_depth:
            # Call LLM
            try:
                async with performance.track_operation(
                    f"llm_call_{depth+1}",
                    metadata={"model": model, "reasoning": reasoning_effort, "depth": depth}
                ):
                    response = await call_llm(client, messages, tool_json, model, reasoning_effort, depth)
                    
                    # Update performance metrics with LLM details
                    if performance.is_enabled():
                        request_id = performance.get_current_request_id()
                        if request_id:
                            for record in performance.get_records():
                                if record.request_id == request_id and record.operation == f"llm_call_{depth+1}":
                                    record.reasoning_count = response.reasoning_count
                                    record.prompt_tokens = response.usage.get("prompt_tokens")
                                    record.completion_tokens = response.usage.get("completion_tokens")
                                    record.total_tokens = response.usage.get("total_tokens")
                                    break
            
            except Exception as err:
                # Handle LLM errors
                error_msg, messages = handle_llm_error(err, messages)
                if error_msg:  # Unrecoverable error
                    store_session(mgr, session_key, messages, pending, focus)
                    return error_msg
                continue  # Retry with updated messages
            
            # Log response
            log.debug("AI_Response_Text: %s", response.final_text)
            if response.function_calls:
                log.debug("AI_Response_Function_Calls: %s",
                         [(fc.name, fc.arguments) for fc in response.function_calls])
            
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
                    return response.final_text or "OK"
            
            # Execute tools if present
            if response.function_calls:
                tool_results = await validate_and_execute_tools(
                    response.function_calls, spec_map, tried_calls, hass
                )
                
                # Add tool results to message history
                messages.extend(tool_results.messages)
                focus = tool_results.focus or focus
                
                # Check for prompt response (confirm/ask)
                if tool_results.prompt_response:
                    store_session(mgr, session_key, messages, tool_results.prompt_response, focus)
                    return {"prompt_payload": tool_results.prompt_response, "messages": messages}
                
                # Check if all tools failed
                if tool_results.all_failed:
                    store_session(mgr, session_key, messages, pending, focus)
                    return tool_results.error_message
            
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
                return "Depth limit reached. Please try again."
        
        # Fallback if loop exits without returning
        log.error("Agent loop exited without response")
        return "I apologize, but I wasn't able to complete your request."
    
    finally:
        # Always write performance metrics
        if performance.is_enabled():
            record_count = len(performance.get_records())
            if record_count > 0:
                try:
                    if hass:
                        await hass.async_add_executor_job(performance.write_csv)
                    else:
                        performance.write_csv()
                    log.debug("Performance metrics written: %d records", record_count)
                except Exception as err:
                    log.error("Failed to write performance metrics: %s", err, exc_info=True)
