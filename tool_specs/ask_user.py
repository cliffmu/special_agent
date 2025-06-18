"""Ask a clarifying question via prompt_user(kind='clarify')."""
from __future__ import annotations
import logging
import voluptuous as vol
from typing import Dict, Any

from ..agent_core import ToolSpec
from .prompt_user import prompt_user

PARAMS = vol.Schema({vol.Required("question"): str})

async def ask_user(question: str, hass=None) -> Dict[str, Any]:
    log.debug("ask_user: %s", question)
    return await prompt_user(
        prompt=question,
        kind="clarify",
        pending=None,
        hass=hass,
    )

SPEC = ToolSpec(
    name="ask_user",
    description="Ask the user for additional information.",
    parameters=PARAMS,
    returns="dict(speak, kind='clarify')",
    func=ask_user,
)
