"""Ask a clarifying follow‑up question."""
from __future__ import annotations

import logging
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
            "description": "The clarifying question to ask the user"
        }
    },
    "required": ["question"]
}


async def ask_user(question: str, hass=None) -> Dict[str, Any]:
    """
    Return a clarification prompt. The session remains open and there is
    no `pending` payload because no action is blocked on the answer.
    """
    log.debug("ask_user -> %s", question)

    return {
        "speak": question,
        "kind":  "clarify",
    }


SPEC = ToolSpec(
    name="ask_user",
    description="Ask the user for additional information to disambiguate the request.",
    parameters=PARAMS,
    returns="dict(speak, kind='clarify')",
    func=ask_user,
)
