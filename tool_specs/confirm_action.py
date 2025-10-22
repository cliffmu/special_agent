"""Ask the user to confirm before executing an action."""
from __future__ import annotations

import logging
import re
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
            "description": "Natural language confirmation question to speak (use friendly names only, NO entity IDs)"
        }
    },
    "required": ["action", "targets", "question"]
}


def _clean_for_voice(text: str) -> str:
    """Remove technical artifacts from text to make it voice-friendly."""
    # Remove entity IDs (e.g., media_player.office_sonos, light.kitchen_light)
    text = re.sub(r'\b[a-z_]+\.[a-z0-9_]+\b', '', text)
    # Remove parentheses with entity IDs that were removed
    text = re.sub(r'\s*\([^)]*\)\s*', ' ', text)
    # Clean up multiple spaces
    text = re.sub(r'\s+', ' ', text)
    # Clean up punctuation spacing
    text = re.sub(r'\s+([,.!?])', r'\1', text)
    return text.strip()


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

    # Clean up question for voice output
    clean_question = _clean_for_voice(question)
    log.debug("confirm_action -> %s (cleaned: %s) | targets=%s", question, clean_question, targets_list)

    return {
        "speak":   clean_question,
        "kind":    "confirm",
        "pending": {"action": action, "targets": targets_list},
    }


SPEC = ToolSpec(
    name="confirm_action",
    description=(
        "Ask the user for confirmation before changing device states. "
        "Provide a clear, concise question that mentions both the action and target devices."
        "Group similar devices into a single question if possible."
    ),
    parameters=PARAMS,
    returns="dict(speak, kind='confirm', pending)",
    func=confirm_action,
    can_run_parallel=False,  # Needs user response - must run alone
)
