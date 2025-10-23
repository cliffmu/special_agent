"""Call a Home Assistant service to control a device."""

from __future__ import annotations

import logging
from typing import Any

from ..agent_core import ToolSpec
from ..utils import logging as log

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
        },
        "verify_after_seconds": {
            "type": "integer",
            "description": "Wait N seconds then check entity state again to verify action succeeded. Recommended: 3-5 for media players, 1-2 for lights/switches. Omit for instant return.",
            "default": None
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
    """Invoke a Home Assistant service and return detailed status."""
    if hass is None:
        raise RuntimeError("hass required")
    if "." not in service:
        raise ValueError("service must be of the form 'domain.name'")
    
    domain, name = service.split(".", 1)
    
    # Merge entity_id into service data
    service_data = data.copy() if data else {}
    service_data['entity_id'] = entity_id
    
    log.debug("control_device: %s entity=%s data=%s verify_after=%s", service, entity_id, data, verify_after_seconds)
    
    # Check entity state BEFORE action if entity_id provided
    before_state = None
    if entity_id and isinstance(entity_id, str) and hasattr(hass, "states"):
        state_obj = hass.states.get(entity_id)
        if state_obj:
            before_state = state_obj.state
    
    # Call the service
    await hass.services.async_call(domain, name, service_data, blocking=True)
    
    # Check entity state immediately AFTER action
    after_state = None
    if entity_id and isinstance(entity_id, str) and hasattr(hass, "states"):
        state_obj = hass.states.get(entity_id)
        if state_obj:
            after_state = state_obj.state
    
    # Wait and verify if requested
    verified_state = None
    if verify_after_seconds and entity_id and isinstance(entity_id, str):
        import asyncio
        await asyncio.sleep(verify_after_seconds)
        if hasattr(hass, "states"):
            state_obj = hass.states.get(entity_id)
            if state_obj:
                verified_state = state_obj.state
                log.debug("control_device: verified state after %ds: %s", verify_after_seconds, verified_state)
    
    # Build result with state data for agent to interpret
    targets = entity_id if isinstance(entity_id, list) else [entity_id] if entity_id else None
    
    result = {
        "service_called": service,
        "focus": {"targets": targets, "action": service}
    }
    
    # Add state information if we have it
    if entity_id and isinstance(entity_id, str):
        result["entity_id"] = entity_id
        result["before_state"] = before_state
        result["after_state"] = after_state
        
        # Use verified state if available, otherwise immediate state
        final_state = verified_state if verified_state is not None else after_state
        result["available"] = final_state not in ("unavailable", "unknown", None)
        
        # Add verified state to result if we waited
        if verified_state is not None:
            result["verified_state"] = verified_state
            result["verified_after_seconds"] = verify_after_seconds
        
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
                result["note"] = "Entity unavailable - may need related device turned on first"
    
    log.debug("control_device result: %s", result)
    return result


SPEC = ToolSpec(
    name="control_device",
    description=(
        "Call Home Assistant service to control devices. Returns before/after state for validation. "
        "TIMING GUIDE (use with verify_after_seconds for state verification): "
        "Power on (turn_on): 8-10s for Apple TV/Roku, 6s for other devices. "
        "App switching (select_source): 3-4s. "
        "Lights/switches: 1-2s. "
        "Media commands: 4-5s. "
        "If state verification fails but command should work, follow up with get_entity_state after additional delay."
    ),
    parameters=PARAMS,
    returns="dict(service_called, entity_id, before_state, after_state, verified_state?, available, note?, focus)",
    func=control_device,
    can_run_parallel=True,
)
