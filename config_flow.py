from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from . import DOMAIN
from .utils.constants import (
    AGENT_MODELS, DEFAULT_AGENT_MODEL, REASONING_EFFORTS, normalize_reasoning_effort,
)

_SECRET_FIELDS = ("openai_api_key", "spotify_client_secret")


def _schema(current: dict[str, Any], *, setup: bool = False):
    """Never send saved credentials back to the browser."""
    key_field = vol.Required if setup else vol.Optional
    models = list(AGENT_MODELS)
    saved_model = current.get("agent_model", DEFAULT_AGENT_MODEL)
    if saved_model not in models:
        models.append(saved_model)
    password = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
    return vol.Schema({
        key_field("openai_api_key", default=""): password,
        vol.Optional("spotify_client_id", default=current.get("spotify_client_id", "")): str,
        vol.Optional("spotify_client_secret", default=""): password,
        vol.Optional("agent_model", default=saved_model): vol.In(models),
        vol.Optional("reasoning_effort", default=current.get("reasoning_effort", "low")): vol.In(REASONING_EFFORTS),
        vol.Optional("fast_mode", default=current.get("fast_mode", False)): bool,
        vol.Optional("require_confirmation", default=current.get("require_confirmation", True)): bool,
        vol.Optional("session_timeout_minutes", default=current.get("session_timeout_minutes", 5)):
            vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        vol.Optional("enable_performance_tracking", default=current.get("enable_performance_tracking", False)): bool,
        vol.Optional("scene_memory_enabled", default=current.get("scene_memory_enabled", False)): bool,
    })


def _settings(user_input: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    data = {**current, **user_input}
    for field in _SECRET_FIELDS:
        data[field] = user_input.get(field, "").strip() or current.get(field, "")
    model = data.get("agent_model", DEFAULT_AGENT_MODEL)
    data["reasoning_effort"] = normalize_reasoning_effort(model, data.get("reasoning_effort", "low"))
    return data


class SpecialAgentConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup for Special Agent."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            data = _settings(user_input, {})
            if data.get("openai_api_key"):
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Special Agent", data=data)
            errors["openai_api_key"] = "required_key"
        return self.async_show_form(step_id="user", data_schema=_schema(user_input or {}, setup=True), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        # Home Assistant assigns the read-only config_entry property itself.
        return SpecialAgentOptionsFlow()


class SpecialAgentOptionsFlow(config_entries.OptionsFlow):
    """Handle settings without re-entering stored API credentials."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return self.async_create_entry(title="", data=_settings(user_input, current))
        return self.async_show_form(step_id="init", data_schema=_schema(current))
