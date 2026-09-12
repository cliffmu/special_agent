"""Observable command outcomes must not be confused with accepted service calls."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from special_agent.utils.service_verification import call_service_verified


def state(value, **attributes):
    return SimpleNamespace(state=value, attributes=attributes)


def fake_hass(states):
    return SimpleNamespace(
        states=states,
        services=SimpleNamespace(async_call=AsyncMock()),
    )


def test_dispatches_once_and_verifies_reported_requested_setting():
    hass = fake_hass({"light.office": state("off")})
    data = {"entity_id": "light.office", "brightness": 128}

    async def apply_service(*args, **kwargs):
        hass.states["light.office"] = state("on", brightness=128)

    hass.services.async_call.side_effect = apply_service
    result = asyncio.run(call_service_verified(hass, "light.turn_on", data, verify_timeout=0.01,
                                               deadline=time.monotonic() + 0.02))

    hass.services.async_call.assert_awaited_once_with("light", "turn_on", data, blocking=True)
    assert result["accepted"] is True
    assert result["verification"] == "verified"
    assert result["status"] == "ok"
    assert result["verification_basis"] == "home_assistant_reported_state"
    assert result["before_states"] == {"light.office": "off"}
    assert result["checks"][0]["observed"]["attributes"]["brightness"] == 128


def test_one_matching_target_does_not_hide_another_targets_mismatch():
    hass = fake_hass({
        "light.office": state("on", brightness=128),
        "light.hall": state("on", brightness=30),
    })
    data = {"entity_id": ["light.office", "light.hall"], "brightness": 128}

    result = asyncio.run(call_service_verified(hass, "light.turn_on", data, verify_timeout=0.01,
                                               deadline=time.monotonic() + 0.02))

    hass.services.async_call.assert_awaited_once_with("light", "turn_on", data, blocking=True)
    assert result["accepted"] is True
    assert result["verification"] == "failed"
    assert result["status"] == "error"
    checks = {check["entity_id"]: check for check in result["checks"]}
    assert checks["light.office"]["verification"] == "verified"
    assert checks["light.hall"]["verification"] == "failed"
    assert "brightness" in checks["light.hall"]["mismatches"]


@pytest.mark.parametrize("options", [{}, {"verify_timeout": 0}], ids=["omitted", "zero"])
def test_optional_wait_cannot_disable_state_comparison(options):
    hass = fake_hass({"switch.office": state("off")})

    async def run():
        return await call_service_verified(
            hass,
            "switch.turn_on",
            {"entity_id": "switch.office"},
            deadline=time.monotonic() + 0.02,
            **options,
        )

    result = asyncio.run(run())

    hass.services.async_call.assert_awaited_once()
    assert result["accepted"] is True
    assert result["verification"] == "failed"
    assert result["checks"][0]["observed"]["state"] == "off"
    assert "state" in result["checks"][0]["mismatches"]


def test_unsupported_action_is_unverified_even_when_dispatch_succeeds():
    hass = fake_hass({"button.office": state("2026-09-11T12:00:00+00:00")})

    result = asyncio.run(call_service_verified(
        hass, "button.press", {"entity_id": "button.office"}, verify_timeout=0.01
    ))

    hass.services.async_call.assert_awaited_once()
    assert result["accepted"] is True
    assert result["status"] == "unverified"
    assert result["verification"] == "unverified"
    assert result["checks"][0]["verification"] == "unverified"


@pytest.mark.parametrize("reported", [None, state("unknown"), state("unavailable")],
                         ids=["missing", "unknown", "unavailable"])
def test_absent_or_unavailable_telemetry_cannot_verify_success(reported):
    hass = fake_hass({} if reported is None else {"switch.office": reported})

    result = asyncio.run(call_service_verified(
        hass, "switch.turn_on", {"entity_id": "switch.office"}, verify_timeout=0.01
    ))

    hass.services.async_call.assert_awaited_once()
    assert result["accepted"] is True
    assert result["verification"] == "unverified"
    assert result["status"] == "unverified"


def test_missing_requested_attribute_is_unverified_despite_matching_power_state():
    hass = fake_hass({"light.office": state("on")})

    result = asyncio.run(call_service_verified(
        hass, "light.turn_on", {"entity_id": "light.office", "brightness": 128}, verify_timeout=0.01,
        deadline=time.monotonic() + 0.02,
    ))

    hass.services.async_call.assert_awaited_once()
    assert result["accepted"] is True
    assert result["verification"] == "unverified"
    assert "brightness" in result["checks"][0]["missing_attributes"]


def test_already_correct_state_verifies_without_waiting_for_a_change():
    hass = fake_hass({"switch.office": state("on")})

    async def run():
        # The normal verification window exceeds this bound; no change event is needed.
        return await asyncio.wait_for(
            call_service_verified(hass, "switch.turn_on", {"entity_id": "switch.office"}),
            timeout=0.2,
        )

    result = asyncio.run(run())

    hass.services.async_call.assert_awaited_once()
    assert result["before_states"] == {"switch.office": "on"}
    assert result["verification"] == "verified"
    assert result["status"] == "ok"


def test_service_completion_deadline_returns_unknown_without_retry():
    hass = fake_hass({"switch.office": state("off")})

    async def run():
        cancelled = asyncio.Event()

        async def never_completes(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        hass.services.async_call.side_effect = never_completes
        result = await asyncio.wait_for(
            call_service_verified(
                hass,
                "switch.turn_on",
                {"entity_id": "switch.office"},
                verify_timeout=0.01,
                deadline=time.monotonic() + 0.02,
            ),
            timeout=0.5,
        )
        assert cancelled.is_set()
        return result

    result = asyncio.run(run())

    hass.services.async_call.assert_awaited_once()
    assert result["accepted"] is None
    assert result["status"] == "timeout"
    assert result["verification"] == "unverified"
    assert result["reason"] == "service_completion_unknown"


def test_expired_deadline_does_not_dispatch():
    hass = fake_hass({"switch.office": state("off")})

    result = asyncio.run(call_service_verified(
        hass,
        "switch.turn_on",
        {"entity_id": "switch.office"},
        deadline=time.monotonic() - 1,
    ))

    hass.services.async_call.assert_not_awaited()
    assert result["accepted"] is None
    assert result["status"] == "timeout"
    assert result["verification"] == "unverified"
