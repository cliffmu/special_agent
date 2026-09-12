"""Read the final light setting after delayed/ramping HA telemetry, without resends."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from special_agent.tool_specs.control_device import control_device
from special_agent.utils import performance, service_verification


@pytest.fixture
def light(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    clock.monotonic = lambda: clock.now
    updates = []
    states = {"light.test_lamp": SimpleNamespace(state="on", attributes={"brightness": 89})}

    async def sleep(seconds):
        clock.now += seconds
        for at, brightness in updates[:]:
            if at <= clock.now:
                updates.remove((at, brightness))
                states["light.test_lamp"] = SimpleNamespace(state="on", attributes={"brightness": brightness})
        await asyncio.sleep(0)

    monkeypatch.setattr(service_verification, "time", clock)
    monkeypatch.setattr(service_verification, "asyncio", SimpleNamespace(
        sleep=sleep, wait_for=asyncio.wait_for, TimeoutError=asyncio.TimeoutError))
    monkeypatch.setattr(performance, "_enabled", False)
    hass = SimpleNamespace(states=SimpleNamespace(get=states.get),
                           services=SimpleNamespace(async_call=AsyncMock()))
    return SimpleNamespace(clock=clock, updates=updates, states=states, hass=hass)


@pytest.mark.parametrize("optional_wait", [None, 0])
async def test_delayed_35_to_40_percent_returns_latest_settled_reading(light, optional_wait):
    light.updates.append((2.5, 102))
    result = await control_device("light.turn_on", "light.test_lamp", {"brightness_pct": 40},
                                  verify_after_seconds=optional_wait, hass=light.hass)

    assert result["accepted"] is True and result["verification"] == "verified"
    check = result["checks"][0]
    assert check["expected"]["attributes"] == {"brightness": 102, "brightness_pct": 40.0}
    assert check["observed"]["attributes"] == {"brightness": 102, "brightness_pct": 40.0}
    assert check["observed"]["attributes"]["brightness"] == light.states["light.test_lamp"].attributes["brightness"]
    assert check["attribute_units"] == {"brightness": "0-255", "brightness_pct": "percent"}
    assert light.clock.now == 4
    light.hass.services.async_call.assert_awaited_once_with(
        "light", "turn_on", {"entity_id": "light.test_lamp", "brightness_pct": 40}, blocking=True)


async def test_near_target_ramp_must_stop_changing_before_success(light):
    # 99 is inside raw brightness tolerance, but is still a ramp reading.
    light.updates.extend([(2, 99), (3, 102)])
    result = await service_verification.call_service_verified(
        light.hass, "light.turn_on", {"entity_id": "light.test_lamp", "brightness_pct": 40})
    assert result["verification"] == "verified"
    assert result["checks"][0]["observed"]["attributes"]["brightness_pct"] == 40
    assert light.clock.now == 4
    light.hass.services.async_call.assert_awaited_once()


async def test_model_selected_three_second_wait_cannot_end_a_normal_light_ramp(light):
    light.updates.append((3.5, 102))
    result = await control_device("light.turn_on", "light.test_lamp", {"brightness_pct": 40},
                                  verify_after_seconds=3, hass=light.hass)
    assert result["verification"] == "verified"
    assert result["checks"][0]["observed"]["attributes"]["brightness_pct"] == 40
    assert light.clock.now == 5
    light.hass.services.async_call.assert_awaited_once()


async def test_legacy_longer_light_timeout_cannot_extend_five_second_budget(light):
    light.updates.append((6, 102))
    result = await control_device("light.turn_on", "light.test_lamp", {"brightness_pct": 40},
                                  verify_after_seconds=8, hass=light.hass)
    assert result["verification"] == "failed" and light.clock.now == 5
    light.hass.services.async_call.assert_awaited_once()


@pytest.mark.parametrize("before,after", [(102, 102), (89, 102)])
async def test_immediate_correct_readback_does_not_add_settling_delay(light, before, after):
    light.states["light.test_lamp"].attributes["brightness"] = before

    async def dispatch(*args, **kwargs):
        light.states["light.test_lamp"].attributes["brightness"] = after

    light.hass.services.async_call.side_effect = dispatch
    result = await service_verification.call_service_verified(
        light.hass, "light.turn_on", {"entity_id": "light.test_lamp", "brightness_pct": 40})
    assert result["verification"] == "verified" and light.clock.now == 0
    light.hass.services.async_call.assert_awaited_once()


async def test_settling_does_not_overrun_shared_deadline(light):
    light.updates.append((2.5, 102))
    result = await service_verification.call_service_verified(
        light.hass, "light.turn_on", {"entity_id": "light.test_lamp", "brightness_pct": 40}, deadline=2.6)
    assert result["verification"] == "unverified"
    assert result["reason"] == result["checks"][0]["reason"] == "state_not_settled"
    assert result["checks"][0]["observed"]["attributes"]["brightness_pct"] == 40
    assert light.clock.now == 2.6
    light.hass.services.async_call.assert_awaited_once()


async def test_long_transition_stays_unverified_at_five_second_limit(light):
    light.updates.append((7.5, 102))
    result = await service_verification.call_service_verified(light.hass, "light.turn_on", {
        "entity_id": "light.test_lamp", "brightness_pct": 40, "transition": 8})
    assert result["verification"] == "unverified" and light.clock.now == 5
    assert result["checks"][0]["reason"] == "transition_in_progress"
    light.hass.services.async_call.assert_awaited_once()


async def test_raw_brightness_is_not_treated_as_percent(light):
    light.states["light.test_lamp"].attributes["brightness"] = 40
    result = await service_verification.call_service_verified(
        light.hass, "light.turn_on", {"entity_id": "light.test_lamp", "brightness": 40})
    check = result["checks"][0]
    assert result["verification"] == "verified"
    assert check["expected"]["attributes"] == check["observed"]["attributes"] == {
        "brightness": 40, "brightness_pct": 15.7}
    assert light.clock.now == 0


async def test_unchanged_mismatch_still_fails_at_bounded_default(light):
    result = await service_verification.call_service_verified(
        light.hass, "light.turn_on", {"entity_id": "light.test_lamp", "brightness_pct": 40})
    assert result["accepted"] is True and result["verification"] == "failed"
    assert result["checks"][0]["observed"]["attributes"]["brightness_pct"] == 34.9
    assert light.clock.now == 5
    light.hass.services.async_call.assert_awaited_once()
