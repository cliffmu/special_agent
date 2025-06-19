"""Ask a clarifying follow‑up question."""
from __future__ import annotations

import logging
from typing import Dict, Any

import voluptuous as vol

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

PARAMS = vol.Schema({vol.Required("question"): str})


async def ask_user(question: str, hass=None) -> Dict[str, Any]:
    """
    Return a clarification prompt.  The session remains open and there is
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
