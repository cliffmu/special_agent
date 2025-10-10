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
        },
        "verify_after_seconds": {
            "type": "integer",
            "description": "Wait N seconds then check entity state again to verify action succeeded. Recommended: 3-5 for media players, 1-2 for lights/switches. Omit for instant return.",
            "default": None
        }
    },
    "required": ["service"]
}


async def control_device(
    service: str, data: dict | None = None, verify_after_seconds: int | None = None, hass: Any | None = None
) -> dict:
    """Invoke a Home Assistant service and return detailed status."""
    if hass is None:
        raise RuntimeError("hass required")
    if "." not in service:
        raise ValueError("service must be of the form 'domain.name'")
    
    domain, name = service.split(".", 1)
    entity_id = (data or {}).get("entity_id")
    
    log.debug("control_device: %s %s verify_after=%s", service, data, verify_after_seconds)
    
    # Check entity state BEFORE action if entity_id provided
    before_state = None
    if entity_id and isinstance(entity_id, str) and hasattr(hass, "states"):
        state_obj = hass.states.get(entity_id)
        if state_obj:
            before_state = state_obj.state
    
    # Call the service
    await hass.services.async_call(domain, name, data or {}, blocking=True)
    
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
        
        # Use verified state if available, otherwise immediate state
        final_state = verified_state if verified_state is not None else after_state
        result["available"] = final_state not in ("unavailable", "unknown", None)
        
        # Add verified state to result if we waited
        if verified_state is not None:
            result["verified_state"] = verified_state
            result["verified_after_seconds"] = verify_after_seconds
        
        # Flag if entity is unavailable after action
        if final_state in ("unavailable", "unknown"):
            # Special handling for Plex/client entities
            if "plex" in entity_id.lower() and service == "media_player.turn_on":
                result["warning"] = (
                    f"Entity is {final_state} - Plex client entities cannot be turned on directly. "
                    "Ensure parent device (Apple TV/Roku/etc) is on and Plex app is open, "
                    "then send media directly with play_media service."
                )
            else:
                result["warning"] = f"Entity is {final_state} after action - may need related device turned on first"
    
    log.debug("control_device result: %s", result)
    return result


SPEC = ToolSpec(
    name="control_device",
    description=(
        "Call a Home Assistant service like 'light.turn_on'. This tool requires explicit entity_ids. "
        "Returns: {status, entity_id, before_state, after_state, verified_state?, available, warning}. "
        "\n\nVERIFICATION: Use 'verify_after_seconds' parameter to wait N seconds then auto-check state."
        "\n- Media playback (play_media): verify_after_seconds=4 (recommended)"
        "\n- Lights/switches: verify_after_seconds=1"
        "\n- No verification needed: omit parameter"
        "\n- Result includes 'verified_state' field when verification used"
        "\n\nDevice Orchestration for Media Playback:"
        "\n1. For Plex/Emby/Jellyfin: ALWAYS send play_media to the Plex INTEGRATION entity (e.g., 'plex_plex_for_apple_tv_...'), NOT the Apple TV/Roku entity"
        "\n2. If Plex entity unavailable: (a) Ensure parent device (Apple TV/Roku) is on, (b) Open Plex app with select_source on parent, (c) Send play_media to Plex entity anyway with verify_after_seconds=4"
        "\n3. Check 'verified_state' or 'after_state': If 'paused', immediately send media_player.media_play to resume"
        "\n4. Success validation: State should be 'playing', not just 'paused' or 'idle'"
        "\n\nIMPORTANT: Plex entities may stay 'unavailable' until media actually starts playing. "
        "Using verify_after_seconds handles this automatically in a single call."
    ),
    parameters=PARAMS,
    returns="dict(status, entity_id, before_state, after_state, verified_state?, verified_after_seconds?, available, warning, focus)",
    func=control_device,
    can_run_parallel=True,  # Can run in parallel (but usually shouldn't with dependent tools)
)
