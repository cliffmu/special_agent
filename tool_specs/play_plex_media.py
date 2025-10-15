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
        "Play Plex content with automatic Companion API fallback. "
        "Use after setup sequence (power on device, open Plex app, scan clients). "
        "Tries HA media_player.play_media first; if entity unavailable and client_ip provided, "
        "uses Plex Companion API (http://CLIENT_IP:32500/player/playback/playMedia). "
        "Companion works even when HA entity shows 'unavailable'. "
        "Returns: {status, method ('ha_service' or 'companion'), state, message/error}. "
        "For reliable playback, always provide client_ip."
    ),
    parameters=PARAMS,
    returns="dict with status, method, state",
    func=play_plex_media,
    can_run_parallel=False,  # Should happen after setup sequence completes
)

