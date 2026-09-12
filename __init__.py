"""Home Assistant entry for the Special Agent integration."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path

try:
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.helpers.event import async_track_time_interval
except Exception:  # pragma: no cover - during unit tests
    EVENT_HOMEASSISTANT_STOP = "ha_stop"
    async def async_track_time_interval(*args, **kw):
        return None

try:
    from .session_store import SessionManager
    from .utils import performance
    from .utils.constants import DEFAULT_AGENT_MODEL
except Exception:  # pragma: no cover
    from session_store import SessionManager
    from utils import performance
    from utils.constants import DEFAULT_AGENT_MODEL

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
    from .live_api import register_views
    register_views(hass)

    async def reload_service_handler(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_reload(entry.entry_id)

    hass.services.async_register(DOMAIN, "reload", reload_service_handler)
    
    async def export_performance_handler(call: ServiceCall) -> None:
        """Export performance metrics to CSV."""
        if not performance.is_enabled():
            _LOGGER.warning("Performance tracking is not enabled")
            return
        await hass.async_add_executor_job(performance.write_csv)
        _LOGGER.info("Performance metrics exported")
    
    async def clear_performance_handler(call: ServiceCall) -> None:
        """Clear performance metrics."""
        performance.clear_records()
        _LOGGER.info("Performance metrics cleared")
    
    hass.services.async_register(DOMAIN, "export_performance", export_performance_handler)
    hass.services.async_register(DOMAIN, "clear_performance", clear_performance_handler)
    
    async def clear_scene_memory_handler(call: ServiceCall) -> None:
        """Clear all scene memory entries."""
        from .utils import scene_memory_store
        from .utils.vector_index import async_rebuild_scene_index
        
        count = await hass.async_add_executor_job(scene_memory_store.clear_all)
        _LOGGER.info("Cleared %d scene memory entries", count)
        
        # Rebuild empty index
        await async_rebuild_scene_index(hass)
        _LOGGER.info("Scene index rebuilt (empty)")
    
    hass.services.async_register(DOMAIN, "clear_scene_memory", clear_scene_memory_handler)
    
    async def rebuild_scene_index_handler(call: ServiceCall) -> None:
        """Rebuild scene index from scene_memory.json."""
        from .utils.vector_index import async_rebuild_scene_index
        
        _LOGGER.info("Rebuilding scene index from scene_memory.json")
        await async_rebuild_scene_index(hass)
        _LOGGER.info("Scene index rebuilt successfully")
    
    hass.services.async_register(DOMAIN, "rebuild_scene_index", rebuild_scene_index_handler)
    
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Special Agent from a config entry."""
    from .live_api import register_views
    register_views(hass)
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
    
    # Configure performance tracking
    perf_enabled = entry.options.get("enable_performance_tracking", entry.data.get("enable_performance_tracking", False))
    if perf_enabled:
        perf_path = Path(hass.config.path("special_agent_performance.csv"))
        performance.configure(enabled=True, csv_path=perf_path)
        _LOGGER.info("Performance tracking ENABLED: %s", perf_path)
    else:
        performance.configure(enabled=False)
        _LOGGER.debug("Performance tracking DISABLED")
    
    # Register update listener for when options change
    add_listener = getattr(entry, "add_update_listener", None)
    on_unload = getattr(entry, "async_on_unload", None)
    if callable(add_listener) and callable(on_unload):
        on_unload(add_listener(async_reload_entry))
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _save_sessions(_):
        await mgr.save()
        # Write any pending performance metrics
        if performance.is_enabled():
            try:
                await hass.async_add_executor_job(performance.write_csv)
                _LOGGER.debug("Performance metrics saved on shutdown")
            except Exception as err:
                _LOGGER.error("Failed to write performance metrics on shutdown: %s", err)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _save_sessions)
    
    # Get session timeout from config (in minutes, convert to seconds for clear_expired)
    timeout_minutes = entry.options.get("session_timeout_minutes", entry.data.get("session_timeout_minutes", 5))
    timeout_seconds = timeout_minutes * 60
    async_track_time_interval(
        hass, 
        lambda _: mgr.clear_expired(ttl=timeout_seconds), 
        timedelta(hours=1)
    )
    
    _LOGGER.info("Special Agent setup complete. Model: %s, Confirmation: %s, Timeout: %d min", 
                 entry.options.get("agent_model", entry.data.get("agent_model", DEFAULT_AGENT_MODEL)),
                 entry.options.get("require_confirmation", entry.data.get("require_confirmation", True)),
                 timeout_minutes)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).get("conversation_agents", {}).pop(entry.entry_id, None)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options change."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
