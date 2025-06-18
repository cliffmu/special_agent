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
except Exception:  # pragma: no cover
    from session_store import SessionManager

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

    hass.services.async_register(DOMAIN, "reload", reload_service_handler)
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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _save_sessions(_):
        await mgr.save()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _save_sessions)
    async_track_time_interval(hass, lambda _: mgr.clear_expired(), timedelta(hours=1))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
