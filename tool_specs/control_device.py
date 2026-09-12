"""Call a Home Assistant service to control a device."""

from __future__ import annotations

import logging
from typing import Any

from ..agent_core import ToolSpec
from ..utils import logging as log
from ..utils.service_verification import call_service_verified

_LOGGER = logging.getLogger(__package__)

# OpenAI JSON schema format
PARAMS = {
    "type": "object",
    "properties": {
        "service": {
            "type": "string",
            "description": "Home Assistant service to call (e.g., 'light.turn_on', 'switch.turn_off')"
        },
        "entity_id": {
            "type": "string",
            "description": "Entity to control (e.g., 'media_player.gym_atv', 'light.office_lamp')"
        },
        "data": {
            "type": "object",
            "description": "Additional service data (brightness, color, volume, etc). Entity ID is passed separately.",
            "default": {}
        }
    },
    "required": ["service", "entity_id"]
}


async def control_device(
    service: str,
    entity_id: str,
    data: dict | None = None,
    verify_after_seconds: int | None = None,
    hass: Any | None = None
) -> dict:
    """Invoke a service; legacy verify_after_seconds cannot change the fixed wait."""
    if hass is None:
        raise RuntimeError("hass required")
    if "." not in service:
        raise ValueError("service must be of the form 'domain.name'")
    
    # Merge entity_id into service data
    service_data = data.copy() if data else {}
    service_data['entity_id'] = entity_id
    
    log.debug("control_device: %s entity=%s data=%s verify_after=%s", service, entity_id, data, verify_after_seconds)
    
    verification = await call_service_verified(hass, service, service_data, verify_timeout=verify_after_seconds)
    
    # Build result with state data for agent to interpret
    targets = list(verification["before_states"])
    
    result = {
        **verification,
        "service_called": service,
        "focus": {"targets": targets, "action": service}
    }
    
    # Add state information if we have it
    if len(targets) == 1:
        entity_id = targets[0]
        state_obj = hass.states.get(entity_id)
        final_state = state_obj.state if state_obj else None
        result["entity_id"] = entity_id
        result["before_state"] = verification["before_states"].get(entity_id)
        result["after_state"] = final_state
        result["available"] = final_state not in ("unavailable", "unknown", None)
        
        # Add verified state only when the requested result was observed.
        if verification["verification"] == "verified":
            result["verified_state"] = final_state
        
        # Add helpful context for common failure patterns
        if final_state in ("unavailable", "unknown", None):
            # Special handling for Plex/client entities
            if "plex" in entity_id.lower() and service == "media_player.turn_on":
                result["note"] = (
                    "Plex client entities cannot be turned on directly. "
                    "Ensure parent device (Apple TV/Roku/etc) is on and Plex app is open, "
                    "then send media directly with play_media service."
                )
            else:
                result["note"] = "Home Assistant has not reported a usable final state yet."
    
    log.debug("control_device result: %s", result)
    return result


SPEC = ToolSpec(
    name="control_device",
    description=(
        "Call a Home Assistant service once and always verify supported target states/settings. "
        "Python checks immediately, then every second for up to five seconds, returning early on success. "
        "Returns command acceptance separately from verified, failed, or unverified outcomes. "
        "Verification compares HA-reported state, not independent physical proof. "
        "Unsupported commands or missing telemetry remain unverified; report that the outcome is not yet confirmed. "
        "The result already includes polling; do not add follow-up reads solely to repeat verification or resend the command."
    ),
    parameters=PARAMS,
    returns="dict(service_called, accepted, status, verification, verification_basis, checks, before_state, after_state, available, focus)",
    func=control_device,
    can_run_parallel=True,
)
