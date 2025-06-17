"""Ask the user to confirm a planned action."""

from __future__ import annotations

import logging
from typing import List

import voluptuous as vol

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

PARAMS = vol.Schema(
    {
        vol.Required("action"): str,
        vol.Required("targets"): [str],
    }
)


async def confirm_action(action: str, targets: List[str], hass=None) -> str:
    """Return a formatted confirmation question."""
    target_str = ", ".join(targets)
    question = f"Do you want to {action} {target_str}?"
    log.debug("confirm_action: %s", question)
    return question


SPEC = ToolSpec(
    name="confirm_action",
    description="Generate a confirmation question for the user.",
    parameters=PARAMS,
    returns="question string",
    func=confirm_action,
)
