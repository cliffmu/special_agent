"""Device lookup, concurrent history ownership and persisted snapshot ordering."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from special_agent.session_store import Session, SessionBusyError, SessionManager, SessionRequestLocks, session_request_lock
from special_agent.utils import logging as activity_log
from special_agent.utils.session_helpers import get_device_context


@pytest.fixture
def devices(monkeypatch):
    from homeassistant.helpers import device_registry, area_registry

    devices = {
        "uuid-first": SimpleNamespace(name_by_user="First Test Voice", name="Voice PE", area_id="first"),
        "uuid-second": SimpleNamespace(name_by_user="Second Test Voice", name="Voice PE", area_id="second"),
    }
    areas = {"first": SimpleNamespace(name="First test room"), "second": SimpleNamespace(name="Second test room")}
    registry = SimpleNamespace(devices=devices)
    monkeypatch.setattr(device_registry, "async_get", Mock(return_value=registry))
    monkeypatch.setattr(area_registry, "async_get", Mock(return_value=SimpleNamespace(async_get_area=areas.get)))
    return devices


@pytest.mark.parametrize("device_id,expected", [
    ("uuid-first", {"device_name": "First Test Voice", "room": "First test room"}),
    ("uuid-second", {"device_name": "Second Test Voice", "room": "Second test room"}),
    ("first test voice", {"device_name": "First Test Voice", "room": "First test room"}),
    ("First test room", {"device_name": None, "room": None}),
    ("Voice", {"device_name": None, "room": None}),
    ("live:bridge-identity", {"device_name": None, "room": None}),
    ("", {"device_name": None, "room": None}),
])
async def test_device_context_uses_registry_id_or_exact_name_only(devices, device_id, expected):
    assert await get_device_context(object(), ("conversation", device_id)) == expected


async def test_ambiguous_legacy_name_never_picks_the_first_room(devices):
    devices["uuid-second"].name_by_user = "First Test Voice"
    assert await get_device_context(object(), ("conversation", "first test voice")) == {"device_name": None, "room": None}
    assert await get_device_context(object(), ("conversation", "uuid-second")) == {"device_name": "First Test Voice", "room": "Second test room"}


async def test_device_without_area_and_synthetic_id_are_safe(devices):
    devices["uuid-first"].area_id = None
    devices["uuid-second"].name_by_user = "live:bridge-identity"
    assert await get_device_context(object(), ("conversation", "uuid-first")) == {"device_name": "First Test Voice", "room": None}
    assert await get_device_context(object(), ("conversation", "live:bridge-identity")) == {"device_name": None, "room": None}


async def test_waiter_cancellation_does_not_release_the_current_owner():
    registry = SessionRequestLocks()
    entered, release = asyncio.Event(), asyncio.Event()

    async def owner():
        async with registry.hold("session"):
            entered.set()
            await release.wait()

    async def waiter():
        async with registry.hold("session"):
            raise AssertionError("Cancelled waiter must never enter")

    first = asyncio.create_task(owner())
    await entered.wait()
    second = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert registry._entries["session"].lock.locked()
    assert registry._entries["session"].users == 1
    release.set()
    await first
    assert registry._entries == {}


async def test_cancelled_owner_releases_next_waiter_and_removes_registry_key():
    registry = SessionRequestLocks()
    entered, never = asyncio.Event(), asyncio.Event()

    async def owner():
        async with registry.hold("session"):
            entered.set()
            await never.wait()

    async def next_request():
        async with registry.hold("session"):
            return "resumed"

    first = asyncio.create_task(owner())
    await entered.wait()
    second = asyncio.create_task(next_request())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await asyncio.wait_for(second, 1) == "resumed"
    assert registry._entries == {}


async def test_active_key_limit_recovers_and_finished_histories_do_not_accumulate():
    registry = SessionRequestLocks(max_keys=1)
    async with registry.hold("first"):
        with pytest.raises(SessionBusyError):
            async with registry.hold("different"):
                raise AssertionError("Capacity must be enforced before execution")
    for number in range(300):
        with pytest.raises(ValueError):
            async with registry.hold(number):
                raise ValueError("simulated request failure")
    assert registry._entries == {}


async def test_registry_is_shared_across_entity_instances_but_not_entries_or_devices():
    hass = SimpleNamespace(data={})
    async with session_request_lock(hass, "entry", ("conversation", "device-a")):
        async with session_request_lock(hass, "other-entry", ("conversation", "device-a")):
            async with session_request_lock(hass, "entry", ("conversation", "device-b")):
                assert len(hass.data["special_agent"]["session_request_locks"]._entries) == 3
    assert hass.data["special_agent"]["session_request_locks"]._entries == {}


async def test_concurrent_device_saves_cannot_replace_newer_history_with_an_old_snapshot():
    manager = SessionManager(object())
    entered, release = asyncio.Event(), asyncio.Event()
    snapshots = []

    async def save(payload):
        snapshots.append(payload)
        if len(snapshots) == 1:
            entered.set()
            await release.wait()

    manager._store = SimpleNamespace(async_save=AsyncMock(side_effect=save))
    manager.set(("conversation", "device-a"), Session(["A"], None, None, "device-a", 1))
    first = asyncio.create_task(manager.save())
    await entered.wait()
    manager.set(("conversation", "device-b"), Session(["B"], None, None, "device-b", 2))
    second = asyncio.create_task(manager.save())
    await asyncio.sleep(0)
    assert len(snapshots) == 1
    release.set()
    await asyncio.gather(first, second)
    assert set(snapshots[-1]) == {"conversation|device-a", "conversation|device-b"}


async def test_device_log_context_is_parallel_safe_and_raw_values_are_rejected(monkeypatch):
    monkeypatch.setattr(activity_log, "_ACTIVITY_BUFFER", activity_log.ActivityBuffer())

    async def one(device):
        token = activity_log.begin_device(device)
        try:
            await asyncio.sleep(0)
            activity_log.activity("device_request")
        finally:
            activity_log.end_device(token)

    await asyncio.gather(one("private-device-a"), one("private-device-b"))
    activity_log.activity("explicit", device="private-device-a")
    lines = [item["line"] for item in activity_log.read_activity()["records"]]
    assert all("private-device" not in line for line in lines)
    assert f"device={activity_log.device_correlation('private-device-a')}" in lines[0]
    assert f"device={activity_log.device_correlation('private-device-b')}" in lines[1]
    assert "device=redacted" in lines[2]
