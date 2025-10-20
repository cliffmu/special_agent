"""Tool to play Plex content with automatic Companion API fallback."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from ..agent_core import ToolSpec
from ..utils import logging as log
from ..utils.data_sources import get_plex_connection_info, get_integration_entry

_LOGGER = logging.getLogger(__package__)

PARAMS = {
    "type": "object",
    "properties": {
        "rating_key": {
            "type": "string",
            "description": "Plex rating key for the content to play"
        },
        "plex_client_entity": {
            "type": "string",
            "description": "Plex client entity ID (e.g., media_player.plex_plex_for_apple_tv_apple_tv_gym)"
        },
        "client_ip": {
            "type": "string",
            "description": "Client IP address for Companion API fallback (e.g., '192.168.86.208')"
        },
        "parent_device_entity": {
            "type": "string",
            "description": "Parent device entity (e.g., media_player.main_bedroom_atv) to extract IP from"
        },
        "verify_after_seconds": {
            "type": "integer",
            "description": "Wait N seconds then verify playback started (default: 4)",
            "default": 4
        }
    },
    "required": ["rating_key", "plex_client_entity"]
}


async def play_plex_media(
    rating_key: str,
    plex_client_entity: str,
    client_ip: str | None = None,
    parent_device_entity: str | None = None,
    verify_after_seconds: int = 4,
    hass: Any | None = None
) -> Dict[str, Any]:
    """
    Push Plex content to client entity - does NOT handle setup.
    
    Simple: Try HA service, fallback to Companion HTTP if needed.
    Agent must handle setup (power on, open app, scan) separately.
    """
    log.debug("play_plex_media: rating_key=%s, entity=%s, client_ip=%s, parent=%s", 
              rating_key, plex_client_entity, client_ip, parent_device_entity)
    
    if not hass:
        return {"status": "error", "error": "hass instance required"}
    
    # Auto-discover client IP if not provided
    if not client_ip and parent_device_entity:
        from ..utils.data_sources import get_device_ip_from_entity
        discovered_ip = get_device_ip_from_entity(hass, parent_device_entity)
        if discovered_ip:
            client_ip = discovered_ip
            log.info("play_plex_media: Got IP %s from parent_device_entity %s", client_ip, parent_device_entity)
    
    # Try HA service first
    try:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": plex_client_entity,
                "media_content_type": "plex",
                "media_content_id": rating_key
            },
            blocking=True
        )
        
        # Wait and verify
        await asyncio.sleep(verify_after_seconds)
        state_obj = hass.states.get(plex_client_entity)
        
        # Check if playback started
        if state_obj and state_obj.state in ("playing", "paused"):
            log.info("play_plex_media: Success via HA service, state=%s", state_obj.state)
            result = {
                "status": "success",
                "method": "ha_service",
                "state": state_obj.state
            }
            # Include discovered IP so agent can save it
            if client_ip:
                result["client_ip"] = client_ip
            return result
        
        # HA service didn't work - try Companion if client_ip available
        if client_ip:
            log.info("play_plex_media: HA service didn't start playback, trying Companion")
            from ..utils.plex import companion_play_media
            result = await companion_play_media(
                client_ip, rating_key, hass, verify_after_seconds, plex_client_entity
            )
            # Include IP in response so agent can save it
            if "client_ip" not in result:
                result["client_ip"] = client_ip
            return result
        
        # No fallback available - provide helpful message
        if not client_ip:
            return {
                "status": "partial",
                "method": "ha_service",
                "state": state_obj.state if state_obj else "unknown",
                "message": "HA service called but playback not verified. Could not auto-discover device IP for Companion fallback. Provide client_ip parameter or ensure device is registered in HA with IP address."
            }
        else:
            return {
                "status": "partial",
                "method": "ha_service",
                "state": state_obj.state if state_obj else "unknown",
                "message": "HA service called but playback not confirmed. Client may be unavailable - setup needed."
            }
        
    except Exception as err:
        log.error("play_plex_media: HA service failed: %s", err)
        
        # Try Companion fallback if client_ip provided
        if client_ip:
            log.info("play_plex_media: HA failed, trying Companion")
            from ..utils.plex import companion_play_media
            result = await companion_play_media(
                client_ip, rating_key, hass, verify_after_seconds, plex_client_entity
            )
            # Include IP in response so agent can save it
            if "client_ip" not in result:
                result["client_ip"] = client_ip
            return result
        
        return {
            "status": "error",
            "method": "ha_service",
            "error": str(err),
            "message": "HA service failed. Client setup may be needed (power on device, open Plex app, scan clients)."
        }


SPEC = ToolSpec(
    name="play_plex_media",
    description=(
        "Push Plex content to client. Does NOT handle setup - agent must power on, open Plex app first.\n\n"
        "WORKFLOW:\n"
        "1. search_devices(platform='plex', area=ROOM) → get plex_client_entity\n"
        "2. search_devices(platform='apple_tv', area=ROOM) → get parent_device_entity (for IP)\n"
        "3. play_plex_media(rating_key, plex_client_entity, parent_device_entity=parent)\n\n"
        "IP DISCOVERY (priority order):\n"
        "1. parent_device_entity param (if agent found parent device) - PREFERRED\n"
        "2. Auto-guess from plex_client_entity name (fallback)\n"
        "3. client_ip param (if from saved scene)\n\n"
        "Returns: {status, method, state, client_ip}. Save client_ip in scene for reuse."
    ),
    parameters=PARAMS,
    returns="dict with status, method, state",
    func=play_plex_media,
    can_run_parallel=False,
    can_run_in_sequence=True,
)

