"""Home Assistant entry for the Special Agent integration."""

from __future__ import annotations

import logging
import os
from datetime import timedelta

try:
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.helpers.event import async_track_time_interval
except Exception:  # pragma: no cover - during unit tests
    EVENT_HOMEASSISTANT_STOP = "ha_stop"
    async def async_track_time_interval(*args, **kw):
        return None

try:
    from .session_store import SessionManager
    from .utils import logging as log
except Exception:  # pragma: no cover
    from session_store import SessionManager
    from utils import logging as log

_LOGGER = logging.getLogger(__package__)

try:  # during unit tests Home Assistant may not be installed
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.config_entries import ConfigEntry
except ModuleNotFoundError:  # pragma: no cover - fallback stubs
    HomeAssistant = object
    ServiceCall = object
    ConfigEntry = object

DOMAIN = "special_agent"
PLATFORMS = ["conversation"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up via configuration.yaml (unused)."""

    async def reload_service_handler(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_reload(entry.entry_id)

    async def get_sessions_handler(call: ServiceCall) -> None:
        """Service to get current session information."""
        mgr = hass.data.get(DOMAIN, {}).get("sessions")
        if not mgr:
            return

        sessions = {}
        for key, session in mgr._data.items():
            sessions[key] = {
                "device_id": session.device_id,
                "updated": session.updated,
                "message_count": len(session.messages),
                "has_pending": session.pending is not None,
                "focus": session.focus,
                "last_message_preview": session.messages[-1]["content"][:200] + "..." if session.messages else None
            }

        # Log the session info for debugging
        log.info("Current sessions: %s", sessions)

        # You could also store this in a sensor or trigger automations
        hass.states.async_set(f"{DOMAIN}.sessions", str(sessions), {"sessions": sessions})

    async def get_last_interaction_handler(call: ServiceCall) -> None:
        """Service to get the last interaction details."""
        mgr = hass.data.get(DOMAIN, {}).get("sessions")
        if not mgr:
            return

        # Get the most recent session by update time
        recent_session = None
        for session in mgr._data.values():
            if recent_session is None or session.updated > recent_session.updated:
                recent_session = session

        if recent_session:
            log.info("Last interaction: device=%s, messages=%d, pending=%s",
                    recent_session.device_id, len(recent_session.messages),
                    recent_session.pending is not None)
            if recent_session.messages:
                log.info("Last message: %s", recent_session.messages[-1]["content"][:200])

    hass.services.async_register(DOMAIN, "reload", reload_service_handler)
    hass.services.async_register(DOMAIN, "get_sessions", get_sessions_handler)
    hass.services.async_register(DOMAIN, "get_last_interaction", get_last_interaction_handler)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Special Agent from a config entry."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data
    mgr = SessionManager(hass)
    await mgr.load()
    hass.data[DOMAIN]["sessions"] = mgr
    api_key = entry.options.get("openai_api_key") or entry.data.get("openai_api_key")
    if api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = api_key
    sp_id = entry.options.get("spotify_client_id") or entry.data.get("spotify_client_id")
    sp_secret = entry.options.get("spotify_client_secret") or entry.data.get("spotify_client_secret")
    if sp_id and not os.environ.get("SPOTIFY_CLIENT_ID"):
        os.environ["SPOTIFY_CLIENT_ID"] = sp_id
    if sp_secret and not os.environ.get("SPOTIFY_CLIENT_SECRET"):
        os.environ["SPOTIFY_CLIENT_SECRET"] = sp_secret
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _save_sessions(_):
        await mgr.save()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _save_sessions)
    async_track_time_interval(hass, lambda _: mgr.clear_expired(), timedelta(hours=1))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
