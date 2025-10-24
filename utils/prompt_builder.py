"""System prompt construction utilities."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, List

try:
    from .tool_registry import ToolSpec, spec_to_json
    from .vector_index import async_load_vector_meta, DEFAULT_DEVICE_PERSIST_DIR
    from .session_helpers import get_device_context
    from . import logging as log
except ImportError:
    from utils.tool_registry import ToolSpec, spec_to_json
    from utils.vector_index import async_load_vector_meta, DEFAULT_DEVICE_PERSIST_DIR
    from utils.session_helpers import get_device_context
    from utils import logging as log


def format_datetime_context() -> tuple[str, str, int]:
    """Format current date/time with timezone.
    
    Returns:
        Tuple of (date_string, time_string, current_year)
    """
    current_datetime = datetime.now()
    date_str = current_datetime.strftime("%A, %B %d, %Y")
    time_str = current_datetime.strftime("%I:%M %p %Z")
    
    # If no timezone in strftime, try to get it manually
    if not time_str.strip().endswith(('PST', 'PDT', 'EST', 'EDT', 'MST', 'MDT', 'CST', 'CDT')):
        tz_name = time.tzname[time.daylight]
        time_str = current_datetime.strftime("%I:%M %p") + f" {tz_name}"
    
    current_year = current_datetime.year
    return date_str, time_str, current_year


async def get_device_context_block(hass: Any, session_key: tuple) -> str:
    """Get speaking device name/room if available.
    
    Returns:
        Formatted device context block or empty string if unavailable
    """
    device_ctx = await get_device_context(hass, session_key)
    device_name = device_ctx.get("device_name")
    device_room = device_ctx.get("room")
    
    if device_name and device_room:
        return (
            f"\nDEVICE CONTEXT:\n"
            f"- User is speaking through: {device_name}\n"
            f"- Device location: {device_room}\n"
            f"- If user doesn't specify a room (e.g., 'turn on the lights'), assume {device_room}\n"
        )
    return ""


async def format_area_summary(hass: Any) -> tuple[Dict, Dict]:
    """Load and format area/platform summary from vector index.
    
    Returns:
        Tuple of (area_summary, platform_summary) dicts
    """
    meta = await async_load_vector_meta(persist_dir=DEFAULT_DEVICE_PERSIST_DIR, hass=hass)
    area_summary = meta.get("area_summary", {}) if isinstance(meta, dict) else {}
    platform_summary = meta.get("platform_summary", {}) if isinstance(meta, dict) else {}
    return area_summary, platform_summary


def format_tool_instructions(tools: List[ToolSpec]) -> str:
    """Format tools with names and descriptions (exclude parameter schemas).
    
    Parameter schemas are provided via OpenAI API - no need to duplicate.
    Descriptions contain important workflow guidance about WHEN/WHY to use tools.
    
    Returns:
        Tool list with names and descriptions
    """
    tool_list = []
    for t in tools:
        tool_list.append(f"• {t.name}: {t.description}")
    return "\n".join(tool_list)


def format_confirmation_rules(require_confirmation: bool) -> tuple[str, str]:
    """Build confirmation-specific instructions.
    
    Returns:
        Tuple of (confirmation_instructions, parallel_execution_note)
    """
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
    
    return confirmation_instructions, parallel_execution_note


def format_goals(goals: List[str] = None) -> str:
    """Format goals list for prompt.
    
    Returns:
        Formatted goals block or empty string
    """
    if goals:
        goals_fmt = "\n".join(f"{idx+1}. {g}" for idx, g in enumerate(goals))
        return f"\nGOALS:\n{goals_fmt}\n"
    return ""


async def build_system_prompt(
    tools: List[ToolSpec],
    hass: Any,
    session_key: tuple,
    model: str,
    reasoning_effort: str,
    require_confirmation: bool,
    goals: List[str] = None
) -> str:
    """Build complete system prompt with all context.
    
    Args:
        tools: List of tool specifications
        hass: Home Assistant instance
        session_key: Session identifier (conversation_id, device_id)
        model: Model name
        reasoning_effort: Reasoning level
        require_confirmation: Whether confirmation is required
        goals: Optional list of goals
        
    Returns:
        Complete system prompt string
    """
    # Gather all context components
    date_str, time_str, current_year = format_datetime_context()
    device_context_block = await get_device_context_block(hass, session_key)
    area_summary, platform_summary = await format_area_summary(hass)
    tool_instructions = format_tool_instructions(tools)
    confirmation_instructions, parallel_execution_note = format_confirmation_rules(require_confirmation)
    goals_block = format_goals(goals)
    
    # Compose system prompt
    system_prompt = (
        f"CURRENT DATE & TIME: {date_str} at {time_str}\n"
        f"Current year: {current_year} - When dates are mentioned without a year, assume this year\n"
        "Knowledge cutoff: October 2024.\n"
        f"{device_context_block}"
        "You are Special Agent, a smarthome AI.\n\n"
        "=== WORKFLOW: GATHER THEN EXECUTE ===\n"
        "For NEW multi-step requests (media playback, device setup, complex scenes):\n"
        "  IF you don't have the data needed (no recent searches in conversation):\n"
        "    → GATHER FIRST: Fire 4-6 parallel searches to collect ALL options\n"
        "      ✓ Check learned routines (2+ search patterns for coverage)\n"
        "      ✓ Device lookups (all relevant locations/types)\n"
        "      ✓ Media/content searches (all variations)\n"
        "      DO NOT execute yet - just gather in parallel\n"
        "  IF you already have data from recent searches in this conversation:\n"
        "    → EXECUTE: Use existing data, pick best path, execute immediately\n\n"
        "For FOLLOW-UP requests (adjustments to current activity):\n"
        "  → EXECUTE directly - you already have context from previous turns\n"
        "  Examples: 'turn it up', 'pause', 'play something else'\n\n"
        "Simple single-action requests (lights, switches): Always execute directly.\n"
        "========================================\n\n"
        "THOUGHT PROCESS:\n"
        "Before calling tools, include a 'Thought:' line in your MESSAGE TEXT explaining your reasoning.\n"
        "Example: 'Thought: I need to search for the gym media player to play content there.'\n"
        "CRITICAL: 'Thought' goes in message content, NOT as a tool parameter.\n"
        "CRITICAL: Do NOT write tool_calls as JSON text - use the actual tool_calls parameter that OpenAI provides.\n"
        f"{goals_block}"
        "You have an index summary of the home with count of entity types for each area:\n"
        f"{json.dumps(area_summary, indent=2)[:3000]}\n"
        "Integration platforms (see 'platform' field in results):\n"
        f"{json.dumps(platform_summary, indent=2)[:1000]}\n"  # keep ≤4 KB to protect context
        "DEVICE INDEX:\n"
        "- Skim the index summary above before attempting lookups to avoid searching for types that don't exist\n"
        "WEB SEARCH:\n"
        "- Built-in web_search provides real-time sports, weather, news when you need current info after Oct 2024\n\n"
        "AVAILABLE TOOLS:\n"
        f"{tool_instructions}\n"
        "(Parameter schemas provided via API)\n\n"
        f"{confirmation_instructions}"
        "PARALLEL EXECUTION:\n"
        "- When multiple lookups are independent, call them in parallel in ONE response\n"
        "- Only chain tool calls when an output is required by the next call\n"
        "- Keep payloads minimal (IDs, small facts), not full attribute dumps\n"
        f"{parallel_execution_note}"
        f"MODEL: {model} | Reasoning: {reasoning_effort} | Confirmation: {'Enabled' if require_confirmation else 'Disabled'}\n"
        "RETRY LOGIC:\n"
        "- If a call fails, attempt at most one improved call; otherwise proceed to execution or ask once\n"
        "- Never repeat an identical call already tried\n"
        "- Once satisfied, MUST call prepare_voice_response - do NOT just return text\n"
        "When an external action is required, you MAY include both a Thought paragraph and "
        "tool_calls in the same message."
        "\nRULES:\n"
        "- NEVER include entity IDs (e.g., media_player.office_sonos) in questions to users - use friendly names only\n"
        "- NEVER suggest checking external services - use only the tools you have available\n"
        "- If you can't find info, just say you don't have access to that information\n"
        "- ALWAYS call prepare_voice_response for final answers - NEVER return raw text"
    )
    
    log.debug("System_Prompt built: %d characters", len(system_prompt))
    return system_prompt

