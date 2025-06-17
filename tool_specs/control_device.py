"""Call a Home Assistant service to control a device."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

PARAMS = vol.Schema(
    {
        vol.Required("service"): str,
        vol.Optional("data", default={}): dict,
    }
)


async def control_device(service: str, data: dict | None = None, hass: Any | None = None) -> str:
    """Invoke a Home Assistant service."""
    if hass is None:
        raise RuntimeError("hass required")
    if "." not in service:
        raise ValueError("service must be of the form 'domain.name'")
    domain, name = service.split(".", 1)
    log.debug("control_device: %s %s", service, data)
    await hass.services.async_call(domain, name, data or {}, blocking=True)
    return "OK"


SPEC = ToolSpec(
    name="control_device",
    description="Call a Home Assistant service like 'light.turn_on'.",
    parameters=PARAMS,
    returns="'OK' on success",
    func=control_device,
)
