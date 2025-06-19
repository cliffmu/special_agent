"""Ask the user to confirm before executing an action."""
from __future__ import annotations

import logging
from typing import Iterable, Dict, Any

import voluptuous as vol

from ..agent_core import ToolSpec
from ..utils import logging as log     # unified async‑safe logger

_LOGGER = logging.getLogger(__package__)

PARAMS = vol.Schema(
    {
        vol.Required("action"):   str,
        vol.Required("targets"):  vol.Any(str, [str]),
        vol.Required("question"): str,        # crafted by the LLM
    }
)


async def confirm_action(
    action: str,
    targets: Iterable[str] | str,
    question: str,
    hass=None,
) -> Dict[str, Any]:
    """
    Return a payload that ConversationEntity will turn into a spoken
    confirmation question. The agent should suspend until the user replies.
    """
    if isinstance(targets, str):
        targets = [targets]

    log.debug("confirm_action -> %s | targets=%s", question, targets)

    return {
        "speak":   question,
        "kind":    "confirm",
        "pending": {"action": action, "targets": list(targets)},
    }


SPEC = ToolSpec(
    name="confirm_action",
    description=(
        "Ask the user for confirmation before using the 'control_device' tool to change device states. "
        "Provide a natural‑language question meant to be spoken aloud that concisely includes both the "
        "action and target devices."
    ),
    parameters=PARAMS,
    returns="dict(speak, kind='confirm', pending)",
    func=confirm_action,
)
