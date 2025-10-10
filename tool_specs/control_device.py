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
        "data": {
            "type": "object",
            "description": "Service data including entity_id",
            "default": {}
        }
    },
    "required": ["service"]
}


async def control_device(
    service: str, data: dict | None = None, hass: Any | None = None
) -> dict:
    """Invoke a Home Assistant service and return detailed status."""
    if hass is None:
        raise RuntimeError("hass required")
    if "." not in service:
        raise ValueError("service must be of the form 'domain.name'")
    
    domain, name = service.split(".", 1)
    entity_id = (data or {}).get("entity_id")
    
    log.debug("control_device: %s %s", service, data)
    
    # Check entity state BEFORE action if entity_id provided
    before_state = None
    if entity_id and isinstance(entity_id, str) and hasattr(hass, "states"):
        state_obj = hass.states.get(entity_id)
        if state_obj:
            before_state = state_obj.state
    
    # Call the service
    await hass.services.async_call(domain, name, data or {}, blocking=True)
    
    # Check entity state AFTER action
    after_state = None
    if entity_id and isinstance(entity_id, str) and hasattr(hass, "states"):
        state_obj = hass.states.get(entity_id)
        if state_obj:
            after_state = state_obj.state
    
    # Build result with validation info
    targets = entity_id if isinstance(entity_id, list) else [entity_id] if entity_id else None
    result = {
        "status": "called",
        "focus": {"targets": targets, "action": service}
    }
    
    # Add state information if we have it
    if entity_id and isinstance(entity_id, str):
        result["entity_id"] = entity_id
        result["before_state"] = before_state
        result["after_state"] = after_state
        result["available"] = after_state not in ("unavailable", "unknown", None)
        
        # Flag if entity is unavailable after action
        if after_state in ("unavailable", "unknown"):
            # Special handling for Plex/client entities
            if "plex" in entity_id.lower() and service == "media_player.turn_on":
                result["warning"] = (
                    f"Entity is {after_state} - Plex client entities cannot be turned on directly. "
                    "Ensure parent device (Apple TV/Roku/etc) is on and Plex app is open, "
                    "then send media directly with play_media service."
                )
            else:
                result["warning"] = f"Entity is {after_state} after action - may need related device turned on first"
    
    log.debug("control_device result: %s", result)
    return result


SPEC = ToolSpec(
    name="control_device",
    description=(
        "Call a Home Assistant service like 'light.turn_on'. This tool requires explicit entity_ids. "
        "Returns: {status, entity_id, before_state, after_state, available, warning}. "
        "Check 'available' field (True/False) to validate success. "
        "\n\nDevice Orchestration Strategy:"
        "\n- Physical devices (lights, switches, Apple TV, Roku): Can turn_on/turn_off directly"
        "\n- Client entities (Plex players, app integrations): Need parent device on first"
        "\n- Plex/Emby/Jellyfin players: Send play_media EVEN IF unavailable - the media command will activate them"
        "\n\nFor unavailable media players: (1) Check if parent device (same area, similar name) is on, "
        "(2) Turn on parent if needed, (3) For Plex/client entities, open app with select_source if available, "
        "(4) Send play_media directly (entity becomes available when playback starts). "
        "Don't try to turn_on Plex/client entities - they activate when receiving media commands."
    ),
    parameters=PARAMS,
    returns="dict(status, entity_id, before_state, after_state, available, warning, focus)",
    func=control_device,
    can_run_parallel=True,  # Can run in parallel (but usually shouldn't with dependent tools)
)
