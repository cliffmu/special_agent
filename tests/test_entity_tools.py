import asyncio
import importlib
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

from special_agent.agent_core import Agent
import pytest


def test_agent_loads_entity_tools():
    agent = Agent()
    assert "get_entity_state" in agent.tools
    assert "get_entity_history" in agent.tools


async def run_get_state(tool, hass):
    return await tool(["light.kitchen"], attributes=["brightness"], hass=hass)


def test_get_entity_state(monkeypatch):
    from special_agent.tool_specs import get_entity_state as ges

    state_obj = SimpleNamespace(state="on", attributes={"brightness": 200})
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=state_obj)

    result = asyncio.run(run_get_state(ges.get_entity_state, hass))

    assert result["light.kitchen"]["state"] == "on"
    assert result["light.kitchen"]["brightness"] == 200


def test_get_entity_state_accepts_string(monkeypatch):
    from special_agent.tool_specs import get_entity_state as ges

    state_obj = SimpleNamespace(state="on", attributes={"color": "blue"})
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=state_obj)

    result = asyncio.run(ges.get_entity_state("light.kitchen", hass=hass))

    assert result["light.kitchen"]["state"] == "on"
    assert "color" in result["light.kitchen"]


def test_state_empty_list_fails():
    from special_agent.tool_specs import get_entity_state as ges

    with pytest.raises(Exception):
        ges.PARAMS({"entity_ids": []})


def test_get_entity_history(monkeypatch):
    events = [
        SimpleNamespace(state="off", last_changed=datetime(2023, 1, 1, 12, 0)),
        SimpleNamespace(state="on", last_changed=datetime(2023, 1, 1, 13, 0)),
    ]

    def fake_history(hass, start, end, entity_ids, significant):
        return {entity_ids[0]: events}

    history_mod = SimpleNamespace(get_significant_states=fake_history)
    sys.modules["homeassistant.components.history"] = history_mod

    import special_agent.tool_specs.get_entity_history as geh
    importlib.reload(geh)

    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(
        side_effect=lambda func, *a, **kw: func(*a, **kw)
    )

    result = asyncio.run(
        geh.get_entity_history("light.kitchen", lookback_hours=1, limit=1, hass=hass)
    )

    assert result[0]["state"] == "on"
    assert "when" in result[0]


def test_get_entity_history_accepts_list(monkeypatch):
    events = [
        SimpleNamespace(state="off", last_changed=datetime(2023, 1, 1, 12, 0)),
        SimpleNamespace(state="on", last_changed=datetime(2023, 1, 1, 13, 0)),
    ]

    def fake_history(hass, start, end, entity_ids, significant):
        return {entity_ids[0]: events}

    history_mod = SimpleNamespace(get_significant_states=fake_history)
    sys.modules["homeassistant.components.history"] = history_mod

    import special_agent.tool_specs.get_entity_history as geh
    importlib.reload(geh)

    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(
        side_effect=lambda func, *a, **kw: func(*a, **kw)
    )

    result = asyncio.run(
        geh.get_entity_history(["light.kitchen"], hass=hass, limit=1)
    )

    assert result[-1]["state"] == "on"


def test_history_cap_applied(monkeypatch):
    base = datetime(2023, 1, 1, 12, 0)
    events = [
        SimpleNamespace(state="on", last_changed=base + timedelta(minutes=i))
        for i in range(150)
    ]

    def fake_history(hass, start, end, entity_ids, significant):
        return {entity_ids[0]: events}

    sys.modules["homeassistant.components.history"] = SimpleNamespace(
        get_significant_states=fake_history
    )

    import special_agent.tool_specs.get_entity_history as geh
    importlib.reload(geh)

    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(
        side_effect=lambda func, *a, **kw: func(*a, **kw)
    )

    result = asyncio.run(
        geh.get_entity_history(
            "sensor.test",
            lookback_hours=1,
            limit=200,
            hass=hass,
        )
    )

    assert len(result) == geh.MAX_EVENTS


def test_history_iso_range(monkeypatch):
    base = datetime(2023, 1, 1, 0, 0)
    events = [
        SimpleNamespace(state=str(i), last_changed=base + timedelta(hours=i))
        for i in range(4)
    ]

    def fake_history(hass, start, end, entity_ids, significant):
        selected = [e for e in events if (not start or e.last_changed >= start) and (not end or e.last_changed <= end)]
        return {entity_ids[0]: selected}

    sys.modules["homeassistant.components.history"] = SimpleNamespace(
        get_significant_states=fake_history
    )

    import special_agent.tool_specs.get_entity_history as geh
    importlib.reload(geh)

    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(
        side_effect=lambda func, *a, **kw: func(*a, **kw)
    )

    start_iso = (base + timedelta(hours=1, minutes=30)).isoformat()
    end_iso = (base + timedelta(hours=2, minutes=30)).isoformat()

    result = asyncio.run(
        geh.get_entity_history(
            "sensor.test",
            start_iso=start_iso,
            end_iso=end_iso,
            limit=10,
            hass=hass,
        )
    )

    assert all(
        (base + timedelta(hours=1)) < datetime.fromisoformat(r["when"]) < (base + timedelta(hours=3))
        for r in result
    )

