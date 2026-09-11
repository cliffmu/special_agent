"""Model settings and HA-managed options flow, without a running HA instance."""

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from special_agent import agent_core
from special_agent.utils.constants import AGENT_MODELS, DEFAULT_AGENT_MODEL, normalize_reasoning_effort


@pytest.fixture
def config_flow(monkeypatch):
    # conftest stubs voluptuous for legacy tests; these forms need real validation.
    monkeypatch.delitem(sys.modules, "voluptuous", raising=False)
    vol = pytest.importorskip("voluptuous")
    monkeypatch.setitem(sys.modules, "voluptuous", vol)

    class Flow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}

        async def async_set_unique_id(self, unique_id):
            self.unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            pass

    class OptionsFlow(Flow):
        @property
        def config_entry(self):
            # Current HA owns this property; assigning it raises AttributeError.
            return self._managed_entry

    class PasswordSelector:
        def __init__(self, config):
            self.config = config

        def __call__(self, value):
            if not isinstance(value, str):
                raise vol.Invalid("Expected text")
            return value

    entries = ModuleType("homeassistant.config_entries")
    entries.ConfigFlow, entries.OptionsFlow = Flow, OptionsFlow
    monkeypatch.setitem(sys.modules, "homeassistant.config_entries", entries)
    monkeypatch.setattr(sys.modules["homeassistant"], "config_entries", entries, raising=False)
    monkeypatch.setattr(sys.modules["homeassistant.core"], "callback", lambda func: func)
    monkeypatch.setattr(sys.modules["homeassistant.helpers"], "selector", SimpleNamespace(
        TextSelector=PasswordSelector, TextSelectorConfig=dict,
        TextSelectorType=SimpleNamespace(PASSWORD="password"),
    ), raising=False)
    spec = importlib.util.spec_from_file_location(
        "special_agent._model_config_test", Path(agent_core.__file__).with_name("config_flow.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_new_setup_has_all_models_and_standard_terra_defaults(config_flow):
    flow = config_flow.SpecialAgentConfigFlow()
    form = await flow.async_step_user()
    schema = form["data_schema"]
    data = schema({"openai_api_key": "new-key"})
    assert (data["agent_model"], data["reasoning_effort"], data["fast_mode"]) == (
        DEFAULT_AGENT_MODEL, "low", False,
    )
    assert set(AGENT_MODELS) == {
        "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-6-astra",
        "gpt-5", "gpt-5-mini", "gpt-5-nano",
    }
    for model in AGENT_MODELS:
        assert schema({"openai_api_key": "new-key", "agent_model": model})["agent_model"] == model
    created = await flow.async_step_user(data)
    assert created["type"] == "create_entry"
    assert created["data"]["fast_mode"] is False
    missing_key = await flow.async_step_user({"openai_api_key": " "})
    assert missing_key["errors"] == {"openai_api_key": "required_key"}


async def test_options_use_read_only_ha_entry_and_preserve_hidden_credentials(config_flow):
    entry = SimpleNamespace(
        data={"agent_model": "gpt-5-mini", "reasoning_effort": "minimal",
              "openai_api_key": "OLD_KEY", "spotify_client_secret": "SPOTIFY_SECRET"},
        options={"openai_api_key": "CURRENT_KEY", "fast_mode": True},
    )
    flow = config_flow.SpecialAgentConfigFlow.async_get_options_flow(entry)
    flow._managed_entry = entry
    with pytest.raises(AttributeError):
        flow.config_entry = entry
    form = await flow.async_step_init()
    schema = form["data_schema"]
    data = schema({})
    assert data["openai_api_key"] == data["spotify_client_secret"] == ""
    assert data["agent_model"] == "gpt-5-mini" and data["reasoning_effort"] == "minimal"
    for field, validator in schema.schema.items():
        if field.schema in ("openai_api_key", "spotify_client_secret"):
            assert validator.config["type"] == "password"
            assert field.default() == ""
    assert "CURRENT_KEY" not in repr(schema) and "SPOTIFY_SECRET" not in repr(schema)
    saved = (await flow.async_step_init(data))["data"]
    assert saved["openai_api_key"] == "CURRENT_KEY"
    assert saved["spotify_client_secret"] == "SPOTIFY_SECRET"
    assert (saved["agent_model"], saved["reasoning_effort"], saved["fast_mode"]) == (
        "gpt-5-mini", "minimal", True,
    )
    updated = (await flow.async_step_init({
        **data, "agent_model": "gpt-5.6-terra", "fast_mode": False,
        "openai_api_key": " replacement-key ",
    }))["data"]
    assert updated["reasoning_effort"] == "low" and updated["fast_mode"] is False
    assert updated["openai_api_key"] == "replacement-key"
    assert updated["spotify_client_secret"] == "SPOTIFY_SECRET"
    assert entry.options == {"openai_api_key": "CURRENT_KEY", "fast_mode": True}


@pytest.mark.parametrize("model", AGENT_MODELS)
def test_supported_reasoning_efforts_and_invalid_model_switches(model):
    supported = {"minimal", "low", "medium", "high"}
    if model.startswith("gpt-5.6"):
        supported = {"none", "low", "medium", "high", "xhigh", "max"}
    elif model == "gpt-6-astra":
        supported = {"low", "medium", "high", "xhigh", "max"}
    for effort in supported:
        assert normalize_reasoning_effort(model, effort) == effort
    for effort in {"none", "minimal", "xhigh", "max", "invalid"} - supported:
        assert normalize_reasoning_effort(model, effort) == "low"
