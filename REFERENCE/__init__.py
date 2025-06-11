# DOMAIN = "special_agent"
# from .logger_helper import log_to_file
# from homeassistant.core import HomeAssistant, ServiceCall

# # Define which platforms your integration uses (e.g., "conversation")
# PLATFORMS = ["conversation"]

# async def async_setup(hass: HomeAssistant, config: dict):
#     log_to_file("[__init__] async_setup called.")

#     async def reload_service_handler(service: ServiceCall) -> None:
#         log_to_file("[__init__] Reload service called.")
#         # Iterate over all config entries for our integration and reload them.
#         for entry in hass.config_entries.async_entries(DOMAIN):
#             await hass.config_entries.async_reload(entry.entry_id)
#         log_to_file("[__init__] Reload service executed.")

#     hass.services.async_register(DOMAIN, "reload", reload_service_handler)
#     return True

# async def async_setup_entry(hass, entry):
#     log_to_file("[__init__] async_setup_entry called.")
#     # Store the configuration entry data in hass.data for later access.
#     hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data
#     await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
#     log_to_file("[__init__] async_setup_entry finished forwarding to conversation.")
#     return True

# async def async_unload_entry(hass, entry):
#     log_to_file("[__init__] async_unload_entry called.")
#     result = await hass.config_entries.async_forward_entry_unload(entry, "conversation")
#     log_to_file("[__init__] async_unload_entry finished.")
#     return result

###### WORKING 2/19/25
# DOMAIN = "special_agent"
# from .logger_helper import log_to_file

# async def async_setup_entry(hass, entry):
#     log_to_file("[__init__] async_setup_entry called.")
#     # Store the configuration entry data using the entry_id as key.
#     hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data
#     await hass.config_entries.async_forward_entry_setups(entry, ["conversation"])
#     log_to_file("[__init__] async_setup_entry finished forwarding to conversation.")
#     return True

# async def async_unload_entry(hass, entry):
#     log_to_file("[__init__] async_unload_entry called.")
#     result = await hass.config_entries.async_forward_entry_unload(entry, "conversation")
#     log_to_file("[__init__] async_unload_entry finished.")
#     return result






# # custom_components/special_agent/__init__.py

# DOMAIN = "special_agent"

# import asyncio
# import logging
# from homeassistant.core import HomeAssistant, ServiceCall
# from homeassistant.config_entries import ConfigEntry

# from .logger_helper import log_to_file
# from .agent_logic import async_rebuild_database

# _LOGGER = logging.getLogger(__name__)

# async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
#     """Set up the Special Agent integration from a config entry."""

#     # Store config data if needed
#     hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data

#     # Register our "rebuild_database" service:
#     async def handle_rebuild_database(call: ServiceCall):
#         # This runs when someone calls special_agent.rebuild_database
#         log_to_file("[__init__] 'rebuild_database' service called. Spawning background task.")
        
#         # Kick off the heavy-lifting in the background:
#         hass.async_create_task(async_rebuild_database(hass))

#     hass.services.async_register(DOMAIN, "rebuild_database", handle_rebuild_database)

#     # Forward to conversation platform (as your code likely does)
#     await hass.config_entries.async_forward_entry_setups(entry, ["conversation"])
#     log_to_file("[__init__] async_setup_entry finished forwarding to conversation.")
#     return True

# async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
#     """Unload the integration."""
#     log_to_file("[__init__] async_unload_entry called.")
#     # Unregister service? (Optional, or you can leave it)
#     # hass.services.async_remove(DOMAIN, "rebuild_database")
    
#     result = await hass.config_entries.async_forward_entry_unload(entry, "conversation")
#     log_to_file("[__init__] async_unload_entry finished.")
#     return result


# custom_components/special_agent/__init__.py

# import logging
# from homeassistant.core import HomeAssistant
# from homeassistant.config_entries import ConfigEntry

# _LOGGER = logging.getLogger(__name__)

# DOMAIN = "special_agent"

# async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
#     """
#     Set up the Special Agent integration from a config entry.
#     This is called when the user adds or reloads the integration in HA UI.
#     """
#     _LOGGER.debug("[__init__] async_setup_entry called for '%s'.", DOMAIN)

#     # Store the config data so other modules can access it (like API keys)
#     hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data

#     # Optionally register any custom service if needed
#     # (For example, if you want a `special_agent.rebuild_database` service,
#     #  but you prefer to do that from agent_logic, it's also okay.)
#     # hass.services.async_register(DOMAIN, "rebuild_database", handle_rebuild_service)

#     # Forward to the conversation platform (conversation.py)
#     await hass.config_entries.async_forward_entry_setups(entry, ["conversation"])

#     _LOGGER.debug("[__init__] Finished forwarding setup to 'conversation' platform.")
#     return True

# async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
#     """
#     Unload the integration if the user removes it from HA.
#     """
#     _LOGGER.debug("[__init__] async_unload_entry called for '%s'.", DOMAIN)

#     # If you had custom services, you could remove them here
#     # hass.services.async_remove(DOMAIN, "rebuild_database")

#     # Unload from the conversation platform
#     unload_ok = await hass.config_entries.async_unload_platforms(entry, ["conversation"])
#     if unload_ok:
#         hass.data[DOMAIN].pop(entry.entry_id)

#     _LOGGER.debug("[__init__] async_unload_entry completed with result=%s", unload_ok)
#     return unload_ok






# custom_components/special_agent/__init__.py

import logging
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from .logger_helper import log_to_file

DOMAIN = "special_agent"
_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Special Agent integration from a config entry."""
    log_to_file(f"async_setup_entry called for {DOMAIN}")
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data

    # 1) Register the custom service "special_agent.rebuild_database".
    async def handle_rebuild_database(call: ServiceCall) -> None:
        """HA service that spawns the long rebuild job in the event loop."""
        log_to_file("handle_rebuild_database called: will spawn background task.")
        # This runs ON the event loop, so we can do:
        hass.async_create_task(async_rebuild_database(hass))

    hass.services.async_register(DOMAIN, "rebuild_database", handle_rebuild_database)

    # 2) Forward to conversation platform
    await hass.config_entries.async_forward_entry_setups(entry, ["conversation"])
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    log_to_file("async_unload_entry called.")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["conversation"])

    # Optionally remove the service if you prefer
    hass.services.async_remove(DOMAIN, "rebuild_database")

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


#
# This next function is the actual long-running job (the "async" function
# that does your device fetch, entity states fetch, embedding, etc.)
# We'll define it here or you can define it in agent_logic.py or data_sources.py,
# but we have to import it in a cycle-safe way. 
#

async def async_rebuild_database(hass: HomeAssistant) -> None:
    """
    The actual long-running background job, spawned by handle_rebuild_database.
    Must do any synchronous calls in the executor or purely async calls.
    """
    log_to_file("async_rebuild_database: START")

    # Because this might import your agent_logic code, you need to be sure the
    # imports do not cause cyclical references. If so, move it to another file
    # and import it here. Or define everything directly here.

    try:
        from .agent_logic import do_full_rebuild  # A separate "sync or async" method
        result = await do_full_rebuild(hass)  # if do_full_rebuild is async
        log_to_file(f"async_rebuild_database result: {result}")
    except Exception as e:
        log_to_file(f"async_rebuild_database error: {e}")
        _LOGGER.error("async_rebuild_database error: %s", e)

    log_to_file("async_rebuild_database: END")

    return
