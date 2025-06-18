import asyncio
from unittest.mock import AsyncMock, MagicMock

from special_agent.tool_specs.confirm_action import confirm_action
from special_agent.tool_specs.control_device import control_device
from special_agent.agent_core import Agent


def test_agent_loads_control_tools():
    agent = Agent()
    assert "confirm_action" in agent.tools
    assert "control_device" in agent.tools


def test_confirm_action_formats_question():
    result = asyncio.run(
        confirm_action(
            "turn on",
            ["light.kitchen"],
            question="Turn on the kitchen light?",
        )
    )
    assert result["kind"] == "confirm"
    assert "turn on" in result["speak"].lower()
    assert "kitchen" in result["speak"].lower()


def test_control_device_calls_service():
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    asyncio.run(control_device("light.turn_on", {"entity_id": "light.kitchen"}, hass=hass))
    hass.services.async_call.assert_awaited_once_with(
        "light", "turn_on", {"entity_id": "light.kitchen"}, blocking=True
    )
