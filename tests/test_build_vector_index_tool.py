import asyncio
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from special_agent.tool_specs.build_vector_index import build_vector_index_tool


def test_build_vector_index_tool_uses_datasource(monkeypatch):
    hass = MagicMock()
    sample_states = [
        {"entity_id": "light.kitchen", "name": "Kitchen Light", "attributes": {}},
        {"entity_id": "switch.garage", "name": "Garage Switch", "attributes": {}},
    ]

    called = {}

    def fake_get_states(hass_arg):
        called["get"] = True
        assert hass_arg is hass
        return sample_states

    def fake_build(states, force_rebuild=False):
        called["build"] = states
        return None, states

    monkeypatch.setattr(
        "special_agent.tool_specs.build_vector_index.get_ha_states", fake_get_states
    )
    monkeypatch.setattr(
        "special_agent.tool_specs.build_vector_index.build_vector_index", fake_build
    )

    result = asyncio.run(build_vector_index_tool(hass=hass))

    assert result == "rebuilt"
    assert called["get"]
    assert called["build"] == sample_states
