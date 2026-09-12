"""Verify commands against Home Assistant's reported state, without resending them.

This confirms integration telemetry, not independent physical device behavior.
Unsupported commands and absent telemetry are explicitly unverified.
"""

from __future__ import annotations

import asyncio
import math
import time

from .data_sources import call_service_tracked

UNAVAILABLE = {None, "unknown", "unavailable"}
POWER_DOMAINS = {"light", "switch", "fan", "input_boolean", "humidifier"}
VERIFICATION_SECONDS = 5
POLL_SECONDS = 1


async def wait_state(hass, entity_id, in_states=None, not_in=None, attr=None, equals=None, timeout=10):
    """Existing sequence state polling, with unavailable/missing values excluded."""
    if not _number(timeout) or timeout < 0:
        raise ValueError("State wait timeout must be a finite non-negative number")
    deadline = time.monotonic() + timeout
    while True:
        state = hass.states.get(entity_id)
        if state and state.state not in UNAVAILABLE:
            value = state.attributes.get(attr) if attr else state.state
            checks = []
            if in_states is not None:
                checks.append(value in in_states)
            if not_in is not None:
                checks.append(value not in not_in)
            if equals is not None:
                checks.append(value == equals)
            if value is not None and checks and all(checks):
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.5, remaining))


def _targets(data):
    value = data.get("entity_id")
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or "." not in item for item in value):
        return []
    return list(dict.fromkeys(value))


def _number(value):
    return type(value) in (int, float) and abs(value) < 10**15 and math.isfinite(value)


def _expected(service, data, entity, before):
    """Derive only settings with a known, observable HA representation."""
    domain, action = service.split(".", 1)
    target_domain = entity.split(".", 1)[0]
    domain = target_domain if domain == "homeassistant" else domain
    states, attributes, consumed = None, {}, {"entity_id"}
    if domain in POWER_DOMAINS and action in {"turn_on", "turn_off", "toggle"}:
        if action == "toggle":
            if before and before.state in {"on", "off"}:
                states = ["off" if before.state == "on" else "on"]
        else:
            states = ["on" if action == "turn_on" else "off"]
    if domain == "media_player":
        if action == "turn_on":
            states = ["on", "idle", "paused", "playing", "buffering"]
        elif action == "turn_off":
            states = ["off"]
        elif action == "media_play":
            states = ["playing"]
        elif action == "media_pause":
            states = ["paused"]
        for command, parameter, attribute, tolerance in (
            ("select_source", "source", "source", 0),
            ("volume_set", "volume_level", "volume_level", 0.01),
            ("volume_mute", "is_volume_muted", "is_volume_muted", 0),
        ):
            if action == command and parameter in data:
                attributes[attribute] = (data[parameter], tolerance)
                consumed.add(parameter)
    if domain == "light" and action == "turn_on":
        if "brightness" in data:
            attributes["brightness"] = (data["brightness"], 3)
            consumed.add("brightness")
        if "brightness_pct" in data and _number(data["brightness_pct"]):
            attributes["brightness"] = (round(data["brightness_pct"] * 255 / 100), 3)
            consumed.add("brightness_pct")
        if attributes.get("brightness", (None,))[0] == 0:
            states = ["off"]
            attributes.pop("brightness")  # Off lights often omit brightness.
        for parameter, tolerance in (("rgb_color", 3), ("rgbw_color", 3), ("rgbww_color", 3),
                                     ("hs_color", 2), ("xy_color", 0.015),
                                     ("color_temp_kelvin", 100), ("color_temp", 2), ("effect", 0)):
            if parameter in data:
                attributes[parameter] = (data[parameter], tolerance)
                consumed.add(parameter)
    if domain == "light" and action in {"turn_on", "turn_off"}:
        consumed.add("transition")
    if domain == "fan":
        for actions, parameter, tolerance in (
            ({"turn_on", "set_percentage"}, "percentage", 1),
            ({"turn_on", "set_preset_mode"}, "preset_mode", 0),
            ({"oscillate"}, "oscillating", 0),
            ({"set_direction"}, "direction", 0),
        ):
            if action in actions and parameter in data:
                attributes[parameter] = (data[parameter], tolerance)
                consumed.add(parameter)
        if "percentage" in attributes:
            states = ["off" if data["percentage"] == 0 else "on"]
    if domain in {"select", "input_select"} and action == "select_option" and "option" in data:
        states = [data["option"]]
        consumed.add("option")
    unsupported = sorted(set(data) - consumed)
    return states, attributes, unsupported


def _matches(actual, expected, tolerance, attribute):
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)) or len(actual) != len(expected):
            return False
        for index, (left, right) in enumerate(zip(actual, expected)):
            if attribute == "hs_color" and index == 0 and _number(left) and _number(right):
                if abs((left - right + 180) % 360 - 180) > tolerance:
                    return False
            elif not _matches(left, right, tolerance, ""):
                return False
        return True
    if _number(actual) and _number(expected):
        return abs(actual - expected) <= tolerance
    return type(actual) is type(expected) and actual == expected


def _observe(hass, entity, expected):
    states, attributes, unsupported = expected
    state = hass.states.get(entity)
    observed = {"state": state.state if state else None, "attributes": {}}
    result = {"entity_id": entity, "expected": {"states": states, "attributes": {
        name: value for name, (value, _) in attributes.items()}}, "observed": observed}
    if "brightness" in attributes and _number(attributes["brightness"][0]):
        # HA reports brightness on a 0-255 scale. Keep its native value for
        # comparison and provide explicitly labelled percent for spoken results.
        result["attribute_units"] = {"brightness": "0-255", "brightness_pct": "percent"}
        result["expected"]["attributes"]["brightness_pct"] = round(attributes["brightness"][0] * 100 / 255, 1)
    if not state or state.state in UNAVAILABLE:
        return {**result, "verification": "unverified", "reason": "entity_unavailable"}
    if not states and not attributes:
        return {**result, "verification": "unverified", "reason": "unsupported_service"}
    mismatches, missing = [], []
    if states is not None and state.state not in states:
        mismatches.append("state")
    for name, (value, tolerance) in attributes.items():
        if name not in state.attributes or state.attributes[name] is None:
            missing.append(name)
            continue
        actual = state.attributes[name]
        observed["attributes"][name] = actual
        if name == "brightness" and _number(actual):
            observed["attributes"]["brightness_pct"] = round(actual * 100 / 255, 1)
        if not _matches(actual, value, tolerance, name):
            step = state.attributes.get("percentage_step") if name == "percentage" else None
            if _number(step) and step > 1 and _number(actual) and _number(value) and abs(actual - value) <= step:
                # HA uses integration-specific speed buckets. Do not call a
                # rounded setting an exact match or a definite command failure.
                missing.append("percentage_quantized")
            else:
                mismatches.append(name)
    if mismatches:
        return {**result, "verification": "failed", "mismatches": mismatches}
    if missing or unsupported:
        return {**result, "verification": "unverified", "reason": "unsupported_or_missing_attributes",
                "missing_attributes": missing, "unsupported_parameters": unsupported}
    return {**result, "verification": "verified"}


async def call_service_verified(hass, service, data, *, verify_timeout=None, deadline=None, post_condition=None):
    """Submit once, then verify locally for up to five seconds, with no LLM calls.

    Legacy verify_timeout values are accepted but cannot change the fixed budget.
    Explicit scene conditions share the same polling loop and deadline.
    """
    started = time.monotonic()
    deadline = deadline if deadline is not None else started + 30
    if not _number(deadline):
        raise ValueError("Verification deadline must be a finite number")
    if verify_timeout is not None and (not _number(verify_timeout) or verify_timeout < 0):
        raise ValueError("Verification timeout must be a finite non-negative number")
    if post_condition is not None:
        validate_post_condition(post_condition)
    entities = _targets(data)
    before = {entity: hass.states.get(entity) for entity in entities}
    expectations = {entity: _expected(service, data, entity, before[entity]) for entity in entities}
    result = {"accepted": None, "verification": "unverified", "verification_basis": "home_assistant_reported_state",
              "checks": [], "before_states": {entity: state.state if state else None for entity, state in before.items()}}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {**result, "status": "timeout", "reason": "deadline_before_dispatch"}
    domain, action = service.split(".", 1)
    try:
        await asyncio.wait_for(call_service_tracked(hass, domain, action, data, blocking=True), remaining)
        result["accepted"] = True
    except asyncio.TimeoutError:
        return {**result, "status": "timeout", "reason": "service_completion_unknown"}
    except Exception as error:
        return {**result, "accepted": False, "verification": "failed", "status": "error",
                "error": f"Service call failed: {error}"}
    if not entities and post_condition is None:
        return {**result, "status": "unverified", "reason": "no_explicit_entity_targets"}
    # The first read is immediate. Only unconfirmed results spend the fixed
    # polling budget; model arguments and transitions cannot extend it.
    target_domains = {entity.split(".", 1)[0] for entity in entities} if domain == "homeassistant" else {domain}
    transition = data.get("transition", 0)
    transition = transition if "light" in target_domains and _number(transition) and transition > 0 else 0
    transition_end = time.monotonic() + transition
    until = min(deadline, time.monotonic() + VERIFICATION_SECONDS)
    settling_targets = {entity for entity in entities
                        if entity.startswith("light.") and expectations[entity][1]}
    ramp_observed = False
    matching_since, matching_snapshot = None, None
    while True:
        checks = [_observe(hass, entity, expectations[entity]) for entity in entities]
        result["checks"] = checks
        unsupported = all(check.get("reason") == "unsupported_service" for check in checks)
        required_checks = checks
        if post_condition is not None:
            explicit = _observe(hass, post_condition["entity_id"], _post_condition_expected(post_condition))
            result["post_condition_result"] = explicit
            # A scene can define a target for a button or other unsupported
            # action, but cannot erase an observable automatic requirement.
            required_checks = ([explicit] if unsupported else [*checks, explicit])
        now = time.monotonic()
        if any(check["entity_id"] in settling_targets and check["verification"] == "failed" for check in checks):
            ramp_observed = True
        all_verified = all(check["verification"] == "verified" for check in required_checks)
        if all_verified:
            snapshot = [check["observed"] for check in checks if check["entity_id"] in settling_targets]
            if not ramp_observed:
                result.update(status="ok", verification="verified")
                break
            if snapshot != matching_snapshot:
                matching_since, matching_snapshot = now, snapshot
            if now - matching_since >= 0.5:
                result.update(status="ok", verification="verified")
                break
        else:
            matching_since, matching_snapshot = None, None
        # Unsupported services cannot gain a verifiable target by waiting.
        if unsupported and post_condition is None:
            result.update(status="unverified")
            break
        remaining = until - time.monotonic()
        if remaining <= 0:
            if all_verified:
                for check in checks:
                    if check["entity_id"] in settling_targets:
                        check.update(verification="unverified", reason="state_not_settled")
                result["reason"] = "state_not_settled"
            if time.monotonic() < transition_end:
                for check in checks:
                    if check["verification"] == "failed":
                        check.update(verification="unverified", reason="transition_in_progress")
            failed = any(check["verification"] == "failed" for check in required_checks)
            result.update(status="error" if failed else "unverified", verification="failed" if failed else "unverified")
            break
        await asyncio.sleep(min(POLL_SECONDS, remaining))
    if post_condition is not None:
        if result["post_condition_result"]["verification"] == "verified":
            result["post_condition_verified"] = True
        else:
            result.update(status="error", post_condition_failed=True,
                          error="Required post-condition was not verified")
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


def validate_post_condition(condition):
    """Reject unusable explicit checks before a scene dispatches the action."""
    if not isinstance(condition, dict) or not isinstance(condition.get("entity_id"), str):
        raise ValueError("post_condition needs an explicit entity_id")
    if "." not in condition["entity_id"]:
        raise ValueError("post_condition entity_id is invalid")
    has_state = isinstance(condition.get("state"), str) and bool(condition["state"])
    has_attribute = isinstance(condition.get("attribute"), str) and bool(condition["attribute"])
    if not has_state and not has_attribute:
        raise ValueError("post_condition needs state or attribute/value")
    if "attribute" in condition and (not has_attribute or "value" not in condition or condition["value"] is None):
        raise ValueError("post_condition attribute needs a non-null value")
    if "state" in condition and not has_state:
        raise ValueError("post_condition state must be a non-empty string")
    if "timeout_ms" in condition and (not _number(condition["timeout_ms"]) or not 0 < condition["timeout_ms"] <= 30000):
        raise ValueError("post_condition timeout_ms must be between 0 and 30000")


def _post_condition_expected(condition):
    return ([condition["state"]] if "state" in condition else None,
            {condition["attribute"]: (condition["value"], 0)} if "attribute" in condition else {}, [])
