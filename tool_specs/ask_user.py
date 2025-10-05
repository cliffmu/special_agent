"""Ask a clarifying follow‑up question."""
from __future__ import annotations

import logging
import re
from typing import Dict, Any

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

# OpenAI JSON schema format
PARAMS = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "The clarifying question to ask the user (use friendly names only, NO entity IDs)"
        }
    },
    "required": ["question"]
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


async def ask_user(question: str, hass=None) -> Dict[str, Any]:
    """
    Return a clarification prompt. The session remains open and there is
    no `pending` payload because no action is blocked on the answer.
    """
    # Clean up question for voice output
    clean_question = _clean_for_voice(question)
    log.debug("ask_user -> %s (cleaned: %s)", question, clean_question)

    return {
        "speak": clean_question,
        "kind":  "clarify",
    }


SPEC = ToolSpec(
    name="ask_user",
    description=(
        "Ask the user for additional information to disambiguate the request. "
        "IMPORTANT: Use friendly device names only - NO technical entity IDs like 'media_player.office_sonos'. "
        "Example: Say 'Office Sonos' not 'Office Sonos (media_player.office_sonos)'."
    ),
    parameters=PARAMS,
    returns="dict(speak, kind='clarify')",
    func=ask_user,
    can_run_parallel=False,  # Needs user response - must run alone
)
