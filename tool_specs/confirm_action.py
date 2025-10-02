"""Ask the user to confirm before executing an action."""
from __future__ import annotations

import logging
from typing import Iterable, Dict, Any

from ..agent_core import ToolSpec
from ..utils import logging as log     # unified async‑safe logger

_LOGGER = logging.getLogger(__package__)

# OpenAI JSON schema format
PARAMS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "The action to confirm (e.g., 'turn off', 'dim lights')"
        },
        "targets": {
            "type": "string",
            "description": "Target entity or entities (comma-separated if multiple)"
        },
        "question": {
            "type": "string",
            "description": "Natural language confirmation question to speak to the user"
        }
    },
    "required": ["action", "targets", "question"]
}


async def confirm_action(
    action: str,
    targets: str,
    question: str,
    hass=None,
) -> Dict[str, Any]:
    """
    Return a payload that ConversationEntity will turn into a spoken
    confirmation question. The agent should suspend until the user replies.
    """
    # Parse targets - can be comma-separated string or single entity
    if "," in targets:
        targets_list = [t.strip() for t in targets.split(",")]
    else:
        targets_list = [targets]

    log.debug("confirm_action -> %s | targets=%s", question, targets_list)

    return {
        "speak":   question,
        "kind":    "confirm",
        "pending": {"action": action, "targets": targets_list},
    }


SPEC = ToolSpec(
    name="confirm_action",
    description=(
        "Ask the user for confirmation before using the 'control_device' tool to change device states. "
        "REQUIRED: You MUST include a 'question' parameter with the exact natural-language sentence to speak. "
        "The question should concisely mention both the action and target devices. "
        "Example: 'Do you want me to turn off the kitchen lights?'"
    ),
    parameters=PARAMS,
    returns="dict(speak, kind='confirm', pending)",
    func=confirm_action,
)
