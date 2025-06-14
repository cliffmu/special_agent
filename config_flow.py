from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.core import callback

from . import DOMAIN

_LOGGER = logging.getLogger(__package__)


class SpecialAgentConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Special Agent."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Special Agent", data={})

        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return SpecialAgentOptionsFlow(config_entry)


class SpecialAgentOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Special Agent."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__(config_entry)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init")
