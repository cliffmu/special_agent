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
            "description": "Overall timeout in seconds, shared by all actions and verification waits",
            "minimum": 0,
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
        "Execute multi-step workflows with service calls, tool calls, delays, and guards. "
        "PRIMARY METHOD for executing saved scene steps from scene memory.\n\n"
        "WHEN TO USE:\n"
        "✓ Executing commands_list from retrieved scenes (pass as 'sequence' param)\n"
        "✓ Multi-step device workflows requiring delays between actions\n"
        "✓ Conditional logic via guards (skip steps based on current state)\n"
        "✓ Variable substitution (${var_name} replaced with values from 'vars' param)\n\n"
        "STEP FORMATS:\n"
        "1) Service: {\"type\":\"service_call\",\"service\":\"light.turn_on\",\"data\":{\"entity_id\":\"...\"}} \n"
        "2) Delay: {\"type\":\"delay\",\"seconds\":8} \n"
        "3) Tool: {\"type\":\"tool_call\",\"tool\":\"<sequence-safe-tool>\",\"args\":{...},\"result_var\":\"x\",\"result_path\":\"x.y\"} \n"
        "4) Guard: {...,\"only_if_state\":{\"entity_id\":\"media_player.tv\",\"attribute\":\"app_name\",\"not_equals\":\"Plex\"}}\n\n"
        "GUARDS (checked internally, zero LLM overhead):\n"
        "- State: \\\"in\\\":[...] or \\\"not_in\\\":[...] checks entity.state\n"
        "- Attribute: \\\"attribute\\\":\\\"app_name\\\", then \\\"equals\\\"/\\\"not_equals\\\"/\\\"in\\\"/\\\"not_in\\\"\n"
        "- Returns status='skipped' for guarded steps (guards skip delays instantly)\n\n"
        "TOOL CALLS:\n"
        "- Only tools explicitly flagged as sequence-safe may run here (validated on save)\n"
        "- Use 'expect' ({path, equals/not_equals/contains/exists}) to auto-verify outputs\n\n"
        "VERIFICATION: Every service action is sent once and supported states/settings are checked automatically. "
        "Python checks immediately, then every second for up to five seconds per action, returning early on success. "
        "Automatic and explicit checks share this window; the overall sequence deadline can shorten it. "
        "Acceptance alone is not success. Checks use HA-reported state, not independent physical proof. "
        "Unsupported commands/missing telemetry remain unverified; later steps may continue, but the workflow returns partial. "
        "Known mismatches, explicit post-condition failures, and timeouts abort dependent steps. Never blindly retry actions. "
        "Service steps may add post_condition:{entity_id,state,attribute,value}; state and attribute are checked together. "
        "Results already include polling; do not add reads solely to repeat verification. "
        "Returns result='completed'/'partial'/'failed' with verification and counts; steps have status='ok'/'unverified'/'skipped'/'error'/'timeout'."
    ),
    parameters=PARAMS,
    returns="dict(result, status, verification, steps, total_steps, attempted_steps, completed_steps, verified_steps, unverified_steps, unattempted_steps)",
    func=run_sequence,
)
