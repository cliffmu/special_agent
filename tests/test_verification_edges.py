"""Regression checks for verification metadata and bounded timing edges."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from special_agent.utils import performance, run_sequence_executor, service_verification
from special_agent.utils.response_utils import validate_and_execute_tools


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(performance, "_enabled", False)
    states = {"light.first": SimpleNamespace(state="on", attributes={}),
              "light.second": SimpleNamespace(state="on", attributes={})}
    return SimpleNamespace(states=states, hass=SimpleNamespace(
        states=SimpleNamespace(get=states.get), services=SimpleNamespace(async_call=AsyncMock())))


@pytest.mark.parametrize("invalid", [float("inf"), float("nan"), -1, True])
async def test_invalid_sequence_timeout_rejected_before_dispatch(env, invalid):
    with pytest.raises(ValueError):
        await run_sequence_executor.run_sequence(sequence={"steps": [
            {"type": "service_call", "service": "light.turn_on", "data": {"entity_id": "light.first"}},
        ]}, timeout=invalid, hass=env.hass)
    env.hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("invalid", [float("inf"), float("nan"), True])
async def test_invalid_verification_deadline_rejected_before_dispatch(env, invalid):
    with pytest.raises(ValueError):
        await service_verification.call_service_verified(
            env.hass, "light.turn_on", {"entity_id": "light.first"}, deadline=invalid)
    env.hass.services.async_call.assert_not_awaited()


async def test_expired_sequence_after_verified_step_is_not_fully_verified(env, monkeypatch):
    clock = SimpleNamespace(now=0)
    clock.monotonic = lambda: clock.now
    monkeypatch.setattr(service_verification, "time", clock)
    monkeypatch.setattr(run_sequence_executor, "time", clock)

    async def dispatch(*args, **kwargs):
        clock.now = 1

    env.hass.services.async_call.side_effect = dispatch
    result = await run_sequence_executor.run_sequence(sequence={"steps": [
        {"type": "service_call", "service": "light.turn_on", "data": {"entity_id": entity}}
        for entity in env.states
    ]}, timeout=1, hass=env.hass)
    assert result["result"] == "failed" and result["verification"] == "unverified"
    assert result["verified_steps"] == result["attempted_steps"] == result["unattempted_steps"] == 1
    env.hass.services.async_call.assert_awaited_once()


async def test_tool_runner_preserves_normalized_focus_and_unverified_activity(env, caplog):
    caplog.set_level(logging.INFO)
    focus = {"targets": ["light.first", "light.second"], "action": "light.turn_on"}
    result = {"focus": focus, "accepted": True, "status": "unverified", "verification": "unverified"}
    spec = SimpleNamespace(name="control_device", func=AsyncMock(return_value=result),
                           validate=None, can_run_parallel=True)
    call = SimpleNamespace(name=spec.name, arguments='{"entity_id":"light.first, light.second"}', call_id="test")
    output = await validate_and_execute_tools([call], {spec.name: spec}, set(), env.hass)
    assert output.focus == focus
    assert "phase=finished status=unverified" in caplog.text
    assert "verification=unverified" in caplog.text


@pytest.mark.parametrize("data,state,attributes,expected", [
    ({"percentage": 50}, "on", {"percentage": 66, "percentage_step": 100 / 3}, "unverified"),
    ({"rgb_color": [255, 0, 0]}, "on", {"rgb_color": (255, 0, 0)}, "verified"),
    ({"rgb_color": [255, 0, 0]}, "on", {"hs_color": (0, 100)}, "unverified"),
    ({"brightness_pct": 0}, "off", {}, "verified"),
])
async def test_setting_precision_and_missing_representation_are_honest(env, data, state, attributes, expected):
    domain = "fan" if "percentage" in data else "light"
    entity = domain + ".first"
    env.states[entity] = SimpleNamespace(state=state, attributes=attributes)
    result = await service_verification.call_service_verified(
        env.hass, domain + ".turn_on", {"entity_id": entity, **data}, verify_timeout=0.001)
    assert result["verification"] == expected
    env.hass.services.async_call.assert_awaited_once()


async def test_generic_media_turn_on_waits_for_slow_reported_startup(env, monkeypatch):
    clock = SimpleNamespace(now=0)
    clock.monotonic = lambda: clock.now
    entity = "media_player.tv"
    env.states[entity] = SimpleNamespace(state="off", attributes={})

    async def sleep(seconds):
        clock.now += seconds
        if clock.now >= 3:
            env.states[entity] = SimpleNamespace(state="idle", attributes={})
        await asyncio.sleep(0)

    monkeypatch.setattr(service_verification, "time", clock)
    monkeypatch.setattr(service_verification, "asyncio", SimpleNamespace(
        sleep=sleep, wait_for=asyncio.wait_for, TimeoutError=asyncio.TimeoutError))
    result = await service_verification.call_service_verified(
        env.hass, "homeassistant.turn_on", {"entity_id": entity})
    assert result["verification"] == "verified" and clock.now == 3
    env.hass.services.async_call.assert_awaited_once()
