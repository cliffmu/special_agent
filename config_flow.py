from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.core import callback
import voluptuous as vol

from . import DOMAIN

_LOGGER = logging.getLogger(__package__)


class SpecialAgentConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Special Agent."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Special Agent", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("openai_api_key"): str,
                    vol.Optional("spotify_client_id", default=""): str,
                    vol.Optional("spotify_client_secret", default=""): str,
                    vol.Optional("agent_model", default="gpt-5"): vol.In(
                        ["gpt-5", "gpt-5-mini", "gpt-5-nano"]
                    ),
                    vol.Optional("reasoning_effort", default="low"): vol.In(
                        ["minimal", "low", "medium", "high"]
                    ),
                    vol.Optional("require_confirmation", default=True): bool,
                    vol.Optional("session_timeout_minutes", default=5): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=60)
                    ),
                    vol.Optional("enable_performance_tracking", default=False): bool,
                    # Google search fields (commented out - using OpenAI web search instead)
                    # vol.Optional("google_api_key", default=""): str,
                    # vol.Optional("google_cx", default=""): str,
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return SpecialAgentOptionsFlow(config_entry)


class SpecialAgentOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Special Agent."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = dict(self.config_entry.options)
        schema = vol.Schema(
            {
                vol.Required(
                    "openai_api_key",
                    default=current.get(
                        "openai_api_key",
                        self.config_entry.data.get("openai_api_key", ""),
                    ),
                ): str,
                vol.Optional(
                    "spotify_client_id",
                    default=current.get(
                        "spotify_client_id",
                        self.config_entry.data.get("spotify_client_id", ""),
                    ),
                ): str,
                vol.Optional(
                    "spotify_client_secret",
                    default=current.get(
                        "spotify_client_secret",
                        self.config_entry.data.get("spotify_client_secret", ""),
                    ),
                ): str,
                vol.Optional(
                    "agent_model",
                    default=current.get(
                        "agent_model",
                        self.config_entry.data.get("agent_model", "gpt-5"),
                    ),
                ): vol.In(["gpt-5", "gpt-5-mini", "gpt-5-nano"]),
                vol.Optional(
                    "reasoning_effort",
                    default=current.get(
                        "reasoning_effort",
                        self.config_entry.data.get("reasoning_effort", "low"),
                    ),
                ): vol.In(["minimal", "low", "medium", "high"]),
                vol.Optional(
                    "require_confirmation",
                    default=current.get(
                        "require_confirmation",
                        self.config_entry.data.get("require_confirmation", True),
                    ),
                ): bool,
                vol.Optional(
                    "session_timeout_minutes",
                    default=current.get(
                        "session_timeout_minutes",
                        self.config_entry.data.get("session_timeout_minutes", 5),
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                vol.Optional(
                    "enable_performance_tracking",
                    default=current.get(
                        "enable_performance_tracking",
                        self.config_entry.data.get("enable_performance_tracking", False),
                    ),
                ): bool,
                # Google search fields (commented out - using OpenAI web search instead)
                # vol.Optional(
                #     "google_api_key",
                #     default=current.get(
                #         "google_api_key",
                #         self.config_entry.data.get("google_api_key", ""),
                #     ),
                # ): str,
                # vol.Optional(
                #     "google_cx",
                #     default=current.get(
                #         "google_cx",
                #         self.config_entry.data.get("google_cx", ""),
                #     ),
                # ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
