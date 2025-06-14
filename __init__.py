"""Home Assistant entry for the Special Agent integration."""

from __future__ import annotations

import logging
import os

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
    api_key = entry.options.get("openai_api_key") or entry.data.get("openai_api_key")
    if api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = api_key
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
