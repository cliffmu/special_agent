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
    verify_after_seconds: int = 4,
    hass: Any | None = None
) -> Dict[str, Any]:
    """
    Push Plex content to client entity - does NOT handle setup.
    
    Simple: Try HA service, fallback to Companion HTTP if needed.
    Agent must handle setup (power on, open app, scan) separately.
    """
    log.debug("play_plex_media: rating_key=%s, entity=%s, client_ip=%s", 
              rating_key, plex_client_entity, client_ip)
    
    if not hass:
        return {"status": "error", "error": "hass instance required"}
    
    # Auto-discover client IP if not provided
    if not client_ip:
        from ..utils.data_sources import get_device_ip_from_entity
        # Try to find parent device to get IP
        if "plex" in plex_client_entity.lower():
            parts = plex_client_entity.split("_")
            if len(parts) > 3:
                area_hint = parts[-1]
                possible_atv = f"media_player.{area_hint}_atv"
                discovered_ip = get_device_ip_from_entity(hass, possible_atv)
                if discovered_ip:
                    client_ip = discovered_ip
                    log.info("play_plex_media: Auto-discovered IP %s", client_ip)
    
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
            return {
                "status": "success",
                "method": "ha_service",
                "state": state_obj.state
            }
        
        # HA service didn't work - try Companion if client_ip available
        if client_ip:
            log.info("play_plex_media: HA service didn't start playback, trying Companion")
            from ..utils.plex import companion_play_media
            result = await companion_play_media(
                client_ip, rating_key, hass, verify_after_seconds, plex_client_entity
            )
            return result
        
        # No fallback available
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
        "Push Plex content to Plex client entity. Simple playback only - does NOT handle device setup.\n\n"
        "PREREQUISITES (agent must handle separately):\n"
        "- Parent device powered on (Apple TV, Roku, etc.)\n"
        "- Plex app open on device (use media_player.select_source with source='Plex')\n"
        "- Plex clients scanned (press button with 'scan clients' in name)\n\n"
        "FINDING PLEX CLIENT:\n"
        "search_devices(query='plex', area='gym', platform='plex') returns Plex CLIENT entity (platform='plex').\n"
        "Use that entity, NOT parent device.\n\n"
        "WHAT THIS TOOL DOES:\n"
        "Tries HA media_player.play_media service. If fails/unavailable, uses Plex Companion HTTP API.\n"
        "Auto-discovers client_ip from device registry if not provided.\n\n"
        "Returns: {status, method, state, message/error}.\n"
        "If status='error' with 'unavailable': Setup needed first (power on, open app, scan)."
    ),
    parameters=PARAMS,
    returns="dict with status, method, state",
    func=play_plex_media,
    can_run_parallel=False,
)

