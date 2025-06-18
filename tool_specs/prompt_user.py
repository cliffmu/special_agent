from __future__ import annotations
import logging
import voluptuous as vol
from typing import Any, Dict

from ..agent_core import ToolSpec
from ..utils import logging as log

PARAMS = vol.Schema({
    vol.Required("prompt"): str,
    vol.Required("kind"):  vol.In(["confirm", "clarify", "notify"]),
    vol.Optional("pending"): dict,
})

async def prompt_user(prompt: str, kind: str,
                      pending: Dict[str, Any] | None = None, hass=None) -> Dict[str, Any]:
    log.debug("prompt_user(%s): %s", kind, prompt)
    return {"speak": prompt, "kind": kind, "pending": pending}

SPEC = ToolSpec(
    name="prompt_user",
    description="Speak a prompt to the user and pause the agent.",
    parameters=PARAMS,
    returns="dict(speak, kind, pending?)",
    func=prompt_user,
)
