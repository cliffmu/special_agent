"""Tool spec for executing saved or inline sequences."""
from __future__ import annotations

from typing import Any, Dict

from ..utils.run_sequence_executor import run_sequence as _execute_sequence
from ..utils.tool_registry import ToolSpec

PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "sequence_ref": {
            "type": "string",
            "description": "Reference to saved sequence in preferences (e.g., 'play_on_screen.v1')",
        },
        "sequence": {
            "type": "object",
            "description": "Inline sequence definition with steps array",
        },
        "vars": {
            "type": "object",
            "description": "Variables for ${substitution} in sequence steps",
        },
        "timeout": {
            "type": "integer",
            "description": "Overall timeout in seconds",
            "default": 30,
        },
    },
    "required": [],
}


async def run_sequence(
    sequence_ref: str | None = None,
    sequence: Dict | None = None,
    vars: Dict | None = None,
    timeout: int = 30,
    hass: Any | None = None,
) -> Dict[str, Any]:
    """Proxy to the shared executor so tool spec stays lightweight."""

    return await _execute_sequence(
        sequence_ref=sequence_ref,
        sequence=sequence,
        vars=vars,
        timeout=timeout,
        hass=hass,
    )


SPEC = ToolSpec(
    name="run_sequence",
    description=(
        "Execute multi-step sequence with service calls, tool calls, delays, and guards. "
        "Guards skip steps instantly (~1ms check).\n\n"
        "STEP FORMATS:\n"
        "1) Service: {\"type\":\"service_call\",\"service\":\"light.turn_on\",\"data\":{\"entity_id\":\"...\"}} \n"
        "2) Delay: {\"type\":\"delay\",\"seconds\":8} \n"
        "3) Tool: {\"type\":\"tool_call\",\"tool\":\"<sequence-safe-tool>\",\"args\":{...},\"result_var\":\"x\",\"result_path\":\"x.y\"} \n"
        "4) Guard: {...,\"only_if_state\":{\"entity_id\":\"media_player.tv\",\"attribute\":\"app_name\",\"not_equals\":\"Plex\"}}\n\n"
        "GUARDS (checked internally, zero LLM overhead):\n"
        "- State: \\\"in\\\":[...] or \\\"not_in\\\":[...] checks entity.state\n"
        "- Attribute: \\\"attribute\\\":\\\"app_name\\\", then \\\"equals\\\"/\\\"not_equals\\\"/\\\"in\\\"/\\\"not_in\\\"\n"
        "- Returns status='skipped' for guarded steps\n\n"
        "TOOL CALLS:\n"
        "- Only tools explicitly flagged as sequence-safe may run here (validated on save)\n"
        "- Use 'expect' ({path, equals/not_equals/contains/exists}) to auto-verify outputs\n\n"
        "Returns: dict with steps[], each with status='ok'/'skipped'/'error'/'timeout'"
    ),
    parameters=PARAMS,
    returns="dict(result, steps, total_steps, completed_steps)",
    func=run_sequence,
)
