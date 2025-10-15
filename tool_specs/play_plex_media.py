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
    Play Plex content with automatic fallback to Companion API if entity unavailable.
    
    Thin wrapper - delegates to utils for complex logic.
    """
    log.debug("play_plex_media: rating_key=%s, entity=%s, client_ip=%s", 
              rating_key, plex_client_entity, client_ip)
    
    if not hass:
        return {"status": "error", "error": "hass instance required"}
    
    # Check current Plex client state to determine if setup needed
    state_obj = hass.states.get(plex_client_entity)
    current_state = state_obj.state if state_obj else "unknown"
    
    log.debug("play_plex_media: Plex client state=%s", current_state)
    
    # If idle/paused/playing: Plex app is already open, just play
    if current_state in ("idle", "paused", "playing"):
        log.info("play_plex_media: Plex client ready (state=%s), playing directly", current_state)
    # If unavailable: Need to open Plex app first - delegate to util for setup
    elif current_state == "unavailable":
        log.info("play_plex_media: Plex client unavailable, attempting auto-setup")
        from ..utils.plex import setup_and_play_plex
        
        # Auto-discover client IP if not provided
        if not client_ip:
            from ..utils.data_sources import get_device_ip_from_entity
            # Try common patterns to find parent device
            if "plex" in plex_client_entity.lower():
                parts = plex_client_entity.split("_")
                if len(parts) > 3:
                    area_hint = parts[-1]
                    possible_atv = f"media_player.{area_hint}_atv"
                    discovered_ip = get_device_ip_from_entity(hass, possible_atv)
                    if discovered_ip:
                        client_ip = discovered_ip
                        log.info("play_plex_media: Auto-discovered IP %s", client_ip)
        
        # Delegate to util for intelligent setup + play
        return await setup_and_play_plex(
            rating_key, plex_client_entity, client_ip, verify_after_seconds, hass
        )
    
    # Try standard HA service first
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
        if state_obj and state_obj.state in ("playing", "paused", "idle"):
            log.info("play_plex_media: Success via HA service, state=%s", state_obj.state)
            return {
                "status": "success",
                "method": "ha_service",
                "state": state_obj.state
            }
        
        # Still unavailable - try Companion if client_ip provided
        if state_obj and state_obj.state == "unavailable" and client_ip:
            log.info("play_plex_media: HA unavailable, trying Companion fallback")
            from ..utils.plex import companion_play_media
            result = await companion_play_media(
                client_ip, rating_key, hass, verify_after_seconds, plex_client_entity
            )
            result["method"] = "companion"
            return result
        
        # Success but state unclear
        return {
            "status": "success",
            "method": "ha_service",
            "state": state_obj.state if state_obj else "unknown"
        }
        
    except Exception as err:
        log.error("play_plex_media: HA service failed: %s", err)
        
        # Try Companion fallback if client_ip provided
        if client_ip:
            log.info("play_plex_media: HA failed, trying Companion fallback")
            from ..utils.plex import companion_play_media
            result = await companion_play_media(
                client_ip, rating_key, hass, verify_after_seconds, plex_client_entity
            )
            result["method"] = "companion"
            return result
        
        return {
            "status": "error",
            "method": "ha_service",
            "error": str(err)
        }


SPEC = ToolSpec(
    name="play_plex_media",
    description=(
        "Play Plex content on Plex client entity. Tool handles setup automatically.\n\n"
        "FINDING THE RIGHT ENTITY:\n"
        "Use search_devices with platform='plex' in target area to find Plex CLIENT entity:\n"
        "  search_devices(query='plex', area='gym', platform='plex')\n"
        "Returns entities with platform='plex' (these are Plex clients, NOT parent devices).\n"
        "Platform field in results identifies entity type: platform='plex' vs platform='apple_tv'.\n\n"
        "WHAT TOOL DOES:\n"
        "Checks Plex client state:\n"
        "- If idle/playing: Ready, plays directly\n"
        "- If unavailable: Auto-setup sequence (power on parent, open Plex app, scan clients, play)\n"
        "Auto-discovers parent device, client IP, Plex source name, scan button.\n"
        "Skips steps already done (checks states first).\n"
        "Uses Companion API fallback if HA service fails.\n\n"
        "Returns: {status, method, state, steps_performed}."
    ),
    parameters=PARAMS,
    returns="dict with status, method, state, steps_performed",
    func=play_plex_media,
    can_run_parallel=False,
)

