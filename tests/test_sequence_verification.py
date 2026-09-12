"""Actual control/sequence wiring with reported HA state and a virtual clock."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from special_agent.tool_specs.control_device import control_device
from special_agent.tool_specs.run_sequence import run_sequence
from special_agent.utils import performance, run_sequence_executor, service_verification


@pytest.fixture
def environment(monkeypatch):
    states, scheduled = {}, []

    class Clock:
        now = 0.0

        def __init__(self):
            self.sleeps = []

        def monotonic(self):
            return self.now

        async def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += max(0, seconds)
            for at, callback in scheduled[:]:
                if at <= self.now:
                    scheduled.remove((at, callback))
                    callback()
            await asyncio.sleep(0)

    class TimedAsyncio:
        def __init__(self, clock):
            self.sleep = clock.sleep

        def __getattr__(self, name):
            return getattr(asyncio, name)

    def set_state(entity, state, **attributes):
        states[entity] = SimpleNamespace(state=state, attributes=attributes)

    clock = Clock()
    for module in (service_verification, run_sequence_executor):
        monkeypatch.setattr(module, "time", clock)
        monkeypatch.setattr(module, "asyncio", TimedAsyncio(clock))
    monkeypatch.setattr(performance, "_enabled", False)
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    return SimpleNamespace(hass=hass, clock=clock, states=states, set_state=set_state, scheduled=scheduled)


def service_step(service="light.turn_on", entity="light.first", **extra):
    return {"type": "service_call", "service": service, "data": {"entity_id": entity}, **extra}


@pytest.mark.parametrize("targets", [["light.first", "light.second"], "light.first, light.second"])
async def test_direct_control_verifies_all_targets_without_false_unavailable_note(environment, targets):
    env = environment
    for entity in ("light.first", "light.second"):
        env.set_state(entity, "off")

    async def switch_on(*args, **kwargs):
        for entity in env.states:
            env.set_state(entity, "on")

    env.hass.services.async_call.side_effect = switch_on
    result = await control_device("light.turn_on", targets, verify_after_seconds=0, hass=env.hass)

    assert result["accepted"] is True and result["verification"] == "verified"
    assert {check["entity_id"] for check in result["checks"]} == {"light.first", "light.second"}
    assert result["focus"]["targets"] == ["light.first", "light.second"]
    assert "note" not in result and result.get("available", True) is True
    env.hass.services.async_call.assert_awaited_once()


async def test_sequence_always_verifies_service_steps_without_explicit_post_conditions(environment):
    env = environment
    env.set_state("light.first", "off")

    async def change_state(domain, action, data, **kwargs):
        env.set_state(data["entity_id"], "on" if action == "turn_on" else "off")

    env.hass.services.async_call.side_effect = change_state
    result = await run_sequence(sequence={"steps": [service_step(), service_step("light.turn_off")]}, hass=env.hass)

    assert result["result"] == "completed"
    assert all(step["accepted"] is True and step["verification"] == "verified" for step in result["steps"])
    assert env.hass.services.async_call.await_count == 2


async def test_known_state_mismatch_aborts_before_next_action_without_resend(environment):
    env = environment
    env.set_state("light.first", "off")
    env.set_state("switch.second", "off")
    result = await run_sequence(sequence={"steps": [service_step(), service_step("switch.turn_on", "switch.second")]}, hass=env.hass)

    assert result["result"] == "failed"
    assert len(result["steps"]) == 1 and result["steps"][0]["verification"] == "failed"
    env.hass.services.async_call.assert_awaited_once()
    assert env.clock.now == pytest.approx(5)


async def test_unknown_command_can_continue_but_cannot_report_whole_sequence_verified(environment):
    env = environment
    env.set_state("button.test", "2026-01-01T00:00:00+00:00")
    env.set_state("switch.second", "off")

    async def change_known_target(domain, action, data, **kwargs):
        if domain == "switch":
            env.set_state(data["entity_id"], "on")

    env.hass.services.async_call.side_effect = change_known_target
    result = await run_sequence(sequence={"steps": [service_step("button.press", "button.test"), service_step("switch.turn_on", "switch.second")]}, hass=env.hass)

    assert result["result"] == "partial"
    assert [step["verification"] for step in result["steps"]] == ["unverified", "verified"]
    assert env.hass.services.async_call.await_count == 2


@pytest.mark.parametrize("state,source,verified", [("on", "HDMI 2", True), ("off", "HDMI 2", False), ("on", "HDMI 1", False)])
async def test_explicit_post_condition_checks_state_and_attribute_together_after_substitution(environment, state, source, verified):
    env = environment
    env.set_state("button.test", "ready")
    env.set_state("media_player.test", state, source=source)
    condition = {"entity_id": "${player}", "state": "on", "attribute": "source", "value": "${source}", "timeout_ms": 250}
    result = await run_sequence(
        sequence={"steps": [service_step("button.press", "button.test", post_condition=condition)]},
        vars={"player": "media_player.test", "source": "HDMI 2"}, hass=env.hass,
    )

    assert result["result"] == ("completed" if verified else "failed")
    step = result["steps"][0]
    assert bool(step.get("post_condition_verified")) is verified
    assert bool(step.get("post_condition_failed")) is not verified
    env.hass.services.async_call.assert_awaited_once()


@pytest.mark.parametrize("condition", [
    None, {}, "on", {"entity_id": "light.first", "attribute": "brightness"},
    {"entity_id": "light.first", "state": "on", "timeout_ms": float("inf")},
])
async def test_malformed_post_condition_is_rejected_before_dispatch(environment, condition):
    result = await run_sequence(
        sequence={"steps": [service_step(post_condition=condition)]}, hass=environment.hass,
    )
    assert result["result"] == "failed"
    environment.hass.services.async_call.assert_not_awaited()


async def test_sequence_service_checks_share_one_deadline_across_steps(environment):
    env = environment
    env.set_state("light.first", "off")
    env.set_state("switch.second", "off")

    async def delayed_state(domain, action, data, **kwargs):
        entity = data["entity_id"]
        after = 1 if entity == "light.first" else 3
        env.scheduled.append((after, lambda: env.set_state(entity, "on")))

    env.hass.services.async_call.side_effect = delayed_state
    result = await run_sequence(sequence={"steps": [
        service_step(), service_step("switch.turn_on", "switch.second"), service_step("button.press", "button.never"),
    ]}, timeout=2, hass=env.hass)

    assert result["result"] == "failed"
    assert env.clock.now <= 2
    assert env.hass.services.async_call.await_count == 2
    assert result["steps"][0]["verification"] == "verified"
    assert result["steps"][1]["verification"] == "failed"


async def test_delay_cannot_overrun_sequence_deadline_and_start_later_action(environment, monkeypatch):
    # asyncio.wait_for uses the event loop's real timer; exercise that boundary
    # directly instead of mixing it with the state-polling virtual clock.
    monkeypatch.setattr(run_sequence_executor, "time", time)
    monkeypatch.setattr(run_sequence_executor, "asyncio", asyncio)
    result = await asyncio.wait_for(run_sequence(sequence={"steps": [
        {"type": "delay", "seconds": 10}, service_step(),
    ]}, timeout=0.02, hass=environment.hass), timeout=1)

    assert result["result"] == "failed"
    assert len(result["steps"]) == 1 and result["steps"][0]["status"] == "timeout"
    environment.hass.services.async_call.assert_not_awaited()


async def test_conditional_service_steps_cannot_bypass_mandatory_verification(environment):
    env = environment
    env.set_state("light.first", "off")
    result = await run_sequence(sequence={"steps": [
        {"type": "if", "when": "${run}", "then": [service_step(), service_step("switch.turn_on", "switch.never")]},
    ]}, vars={"run": True}, hass=env.hass)

    assert result["result"] == "failed"
    env.hass.services.async_call.assert_awaited_once()


@pytest.mark.parametrize("domain", ["switch", "fan", "light", "media_player"])
@pytest.mark.parametrize("update_at,elapsed,verified", [
    (0, 0, True), (2.2, 3, True), (5, 5, True), (5.1, 5, False),
])
async def test_power_off_polls_locally_once_per_second_until_success_or_five_seconds(
    environment, domain, update_at, elapsed, verified,
):
    env = environment
    entity = domain + ".test"
    env.set_state(entity, "on")

    async def dispatch(*args, **kwargs):
        env.set_state(entity, "unavailable")
        if update_at == 0:
            env.set_state(entity, "off")
        else:
            env.scheduled.append((update_at, lambda: env.set_state(entity, "off")))

    env.hass.services.async_call.side_effect = dispatch
    result = await control_device(domain + ".turn_off", entity, hass=env.hass)

    assert result["accepted"] is True
    assert result["verification"] == ("verified" if verified else "unverified")
    assert env.clock.now == elapsed
    assert env.clock.sleeps == [1] * elapsed
    env.hass.services.async_call.assert_awaited_once()


@pytest.mark.parametrize("legacy_wait", [None, 0, 1, 30])
async def test_legacy_timeout_cannot_change_five_second_polling_budget(environment, legacy_wait):
    env = environment
    env.set_state("media_player.test", "off")
    result = await control_device("media_player.turn_on", "media_player.test",
                                  verify_after_seconds=legacy_wait, hass=env.hass)
    assert result["verification"] == "failed"
    assert env.clock.now == 5
    assert env.clock.sleeps == [1] * 5
    env.hass.services.async_call.assert_awaited_once()


@pytest.mark.parametrize("post_at,elapsed,verified", [(4, 4, True), (7, 5, False)])
async def test_scene_automatic_and_explicit_checks_share_one_polling_window(
    environment, post_at, elapsed, verified,
):
    env = environment
    env.set_state("switch.test", "off")
    env.set_state("media_player.test", "off")
    env.scheduled.extend([
        (3, lambda: env.set_state("switch.test", "on")),
        (post_at, lambda: env.set_state("media_player.test", "on")),
    ])
    result = await run_sequence(sequence={"steps": [service_step(
        "switch.turn_on", "switch.test", post_condition={
            "entity_id": "media_player.test", "state": "on", "timeout_ms": 30000,
        },
    )]}, hass=env.hass)
    assert result["verification"] == ("verified" if verified else "failed")
    assert result["steps"][0]["checks"][0]["verification"] == "verified"
    assert env.clock.now == elapsed
    assert env.clock.sleeps == [1] * elapsed
    env.hass.services.async_call.assert_awaited_once()


async def test_scene_post_condition_cannot_erase_automatic_mismatch(environment):
    env = environment
    env.set_state("switch.test", "off")
    env.set_state("media_player.test", "on")
    result = await run_sequence(sequence={"steps": [service_step(
        "switch.turn_on", "switch.test", post_condition={"entity_id": "media_player.test", "state": "on"},
    )]}, hass=env.hass)
    assert result["verification"] == "failed"
    assert result["steps"][0]["post_condition_verified"] is True
    assert env.clock.now == 5
    env.hass.services.async_call.assert_awaited_once()


@pytest.mark.parametrize("update_at,verification", [(3, "verified"), (7, "unverified")])
async def test_polling_returns_one_final_result_to_same_agent_model(environment, monkeypatch, update_at, verification):
    from special_agent import agent_core
    from special_agent.tool_specs.control_device import SPEC

    env = environment
    env.set_state("switch.test", "unavailable")
    env.scheduled.append((update_at, lambda: env.set_state("switch.test", "off")))
    messages = [{"role": "user", "content": "Turn it off"}]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(agent_core, "get_async_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(agent_core, "build_system_prompt", AsyncMock(return_value="test"))
    monkeypatch.setattr(agent_core, "load_session", lambda *args: (messages, None, None, None))
    monkeypatch.setattr(agent_core, "store_session", lambda *args: None)
    call = SimpleNamespace(name=SPEC.name, call_id="control", arguments=json.dumps({
        "service": "switch.turn_off", "entity_id": "switch.test",
    }))
    model_calls = AsyncMock(side_effect=[
        SimpleNamespace(output=[], function_calls=[call], final_text=None),
        SimpleNamespace(output=[], function_calls=[], final_text="Final response"),
    ])
    monkeypatch.setattr(agent_core, "call_llm", model_calls)

    result = await agent_core.plan_execute("Turn it off", [SPEC], hass=env.hass, model="gpt-5.6-luna")

    assert result == "Final response"
    assert env.clock.now == min(update_at, 5)
    env.hass.services.async_call.assert_awaited_once()
    # One model call chooses the command; one receives the final observation.
    assert model_calls.await_count == 2
    assert [item.args[3] for item in model_calls.await_args_list] == ["gpt-5.6-luna"] * 2
    outputs = [item for item in messages if item.get("type") == "function_call_output"]
    assert len(outputs) == 1
    assert json.loads(outputs[0]["output"])["verification"] == verification
