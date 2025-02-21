import voluptuous as vol
from homeassistant import config_entries
from .logger_helper import log_to_file

DOMAIN = "special_agent"

class SpecialAgentConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Special Agent."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            log_to_file("[ConfigFlow] User submitted config.")
            await self.async_set_unique_id("special_agent_config")
            self._abort_if_unique_id_configured()
            log_to_file("[ConfigFlow] Creating config entry for Special Agent.")
            return self.async_create_entry(title="Special Agent", data=user_input)
        log_to_file("[ConfigFlow] Showing user form.")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional("openai_api_key", default=""): str,
                vol.Optional("spotify_client_id", default=""): str,
                vol.Optional("spotify_client_secret", default=""): str,
            }),
            errors={}
        )

    @staticmethod
    async def async_get_options_flow(config_entry):
        return SpecialAgentOptionsFlow(config_entry)


class SpecialAgentOptionsFlow(config_entries.OptionsFlow):
    """Allow the user to update their integration options."""
    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = dict(self.config_entry.options or {})
        schema = vol.Schema({
            vol.Optional(
                "openai_api_key",
                default=current.get("openai_api_key", self.config_entry.data.get("openai_api_key", ""))
            ): str,
            vol.Optional(
                "spotify_client_id",
                default=current.get("spotify_client_id", self.config_entry.data.get("spotify_client_id", ""))
            ): str,
            vol.Optional(
                "spotify_client_secret",
                default=current.get("spotify_client_secret", self.config_entry.data.get("spotify_client_secret", ""))
            ): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
