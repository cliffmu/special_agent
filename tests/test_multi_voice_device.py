"""Concurrent satellites keep authentication, PCM and accepted work isolated."""

import asyncio
import base64
import hashlib
import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from experimental.live import device_server
from experimental.live.backend import BackendResult, HomeAssistantBackend, PROCESS_PATH
from experimental.live.devices import VoiceRegistration
from .test_voice_device import TOKEN, control, harness

SECOND_TOKEN = "test-second-token-0123456789abcdef0123456789"
SECOND = VoiceRegistration("second", SECOND_TOKEN, "Second test room")


def settings(**changes):
    return device_server.DeviceSettings(api_key="fake-cloud-key", device_token=TOKEN, **changes)


@pytest.mark.parametrize("changes, message", [
    ({"devices": (VoiceRegistration("primary", SECOND_TOKEN),)}, "IDs must be unique"),
    ({"devices": (VoiceRegistration("second", TOKEN),)}, "tokens must be unique"),
    ({"devices": (VoiceRegistration("second", SECOND_TOKEN, ha_device_id="live:primary"),)}, "HA identities"),
    ({"ha_device_id": "shared-id", "devices": (VoiceRegistration("second", SECOND_TOKEN, ha_device_id="shared-id"),)}, "HA identities"),
    ({"device_id": "contains spaces"}, "device_id"),
    ({"ha_device_id": "private/id"}, "ha_device_id"),
    ({"devices": ({"id": "second", "token": SECOND_TOKEN},)}, "registration"),
])
def test_invalid_registrations_fail_before_any_device_connects(changes, message):
    with pytest.raises(ValueError, match=message) as error:
        settings(**changes).validate()
    assert TOKEN not in str(error.value) and SECOND_TOKEN not in str(error.value)


def test_registration_limit_includes_primary_and_tokens_stay_out_of_repr():
    extras = tuple(VoiceRegistration(f"device-{number}", f"token-{number:02}-" + "x" * 32)
                   for number in range(16))
    settings(devices=extras[:15]).validate()
    with pytest.raises(ValueError, match="16"):
        settings(devices=extras).validate()
    assert SECOND_TOKEN not in repr(SECOND)
    assert SECOND_TOKEN not in repr(settings(devices=(SECOND,)))


async def start_voice(client, cloud, token, live_id):
    socket = await client.ws_connect("/voice", params={"token": token})
    await socket.send_json({"type": "wake"})
    upstream, start = await asyncio.wait_for(cloud.starts.get(), 2)
    await upstream.send_json({"type": "session.started", "session": {"id": live_id}})
    await control(socket, "ack")
    assert (await control(socket, "phase"))["value"] == "listening"
    return socket, upstream, start, cloud.devices[-1]


async def delegate(upstream, text, job):
    await upstream.send_json({"type": "session.input_transcript.delta", "delta": text})
    await upstream.send_json({"type": "session.delegation.created",
                              "delegation": {"id": job, "target": "client"}})


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 2)


async def test_two_idle_devices_authenticate_independently_and_health_hides_identity(monkeypatch):
    native_id = "synthetic-first-ha-id"
    overrides = {"devices": (SECOND,), "room": "First test room", "ha_device_id": native_id}
    async with harness(monkeypatch, settings_overrides=overrides) as (client, cloud):
        first = await client.ws_connect("/voice", params={"token": TOKEN})
        second = await client.ws_connect("/voice", params={"token": SECOND_TOKEN})
        try:
            for token, expected in ((TOKEN, 409), (SECOND_TOKEN, 409), ("unknown-token", 401)):
                with pytest.raises(aiohttp.WSServerHandshakeError) as error:
                    await client.ws_connect("/voice", params={"token": token})
                assert error.value.status == expected
            health = await (await client.get("/health")).json()
            assert health["device_connected"] and not health["audio_active"]
            assert (health["configured_devices"], health["connected_devices"], health["active_devices"]) == (2, 2, 0)
            assert {row["device"] for row in health["devices"]} == {
                hashlib.sha256(native_id.encode()).hexdigest()[:10], SECOND.device_hash,
            }
            assert all(row["connected"] and not row["draining"] for row in health["devices"])
            public = json.dumps(health)
            for private in (TOKEN, SECOND_TOKEN, native_id, "First test room", SECOND.room, SECOND.id):
                assert private not in public
            assert not cloud.connections  # Idle satellites do not create cloud voice sessions.
        finally:
            await first.close()
            await second.close()


async def test_failed_websocket_prepare_releases_identity_for_the_next_connection(monkeypatch):
    original_prepare = web.WebSocketResponse.prepare
    failed = False

    async def prepare(socket, request):
        nonlocal failed
        if not failed:
            failed = True
            raise web.HTTPServiceUnavailable(text="Handshake unavailable")
        return await original_prepare(socket, request)

    monkeypatch.setattr(web.WebSocketResponse, "prepare", prepare)
    async with harness(monkeypatch) as (client, cloud):
        with pytest.raises(aiohttp.WSServerHandshakeError) as error:
            await client.ws_connect("/voice", params={"token": TOKEN})
        assert error.value.status == 503
        assert not client.app[device_server.REGISTRY].claims
        replacement = await client.ws_connect("/voice", params={"token": TOKEN})
        try:
            health = await (await client.get("/health")).json()
            assert health["connected_devices"] == 1
            assert not cloud.connections
        finally:
            await replacement.close()


async def test_simultaneous_audio_and_interrupts_stay_on_their_own_device(monkeypatch):
    async with harness(monkeypatch, settings_overrides={"devices": (SECOND,)}) as (client, cloud):
        first, cloud_a, _, device_a = await start_voice(client, cloud, TOKEN, "cloud-a")
        second, cloud_b, start_b, device_b = await start_voice(client, cloud, SECOND_TOKEN, "cloud-b")
        try:
            assert device_b.settings.room == SECOND.room
            assert start_b["session"]["model"] == "gpt-live-1"
            assert device_a.session.id != device_b.session.id
            await first.send_bytes(struct.pack("<3h", 0, 300, 600))
            await second.send_bytes(struct.pack("<3h", 1000, 1300, 1600))
            await cloud.next_event("session.input_audio.append")
            await cloud.next_event("session.input_audio.append")
            microphone = {socket: base64.b64decode(event["audio"]) for socket, event in cloud.received
                          if event["type"] == "session.input_audio.append"}
            assert microphone[cloud_a] == struct.pack("<4h", 0, 200, 400, 600)
            assert microphone[cloud_b] == struct.pack("<4h", 1000, 1200, 1400, 1600)
            for socket, pcm in ((cloud_a, b"\x01\x10" * 12), (cloud_b, b"\x02\x20" * 12)):
                await socket.send_json({"type": "session.output_audio.delta", "delta": base64.b64encode(pcm).decode()})
            assert (await asyncio.wait_for(first.receive(), 2)).data == b"\x01\x10" * 12
            assert (await asyncio.wait_for(second.receive(), 2)).data == b"\x02\x20" * 12
            health = await (await client.get("/health")).json()
            assert health["active_devices"] == 2

            await first.send_json({"type": "interrupt"})
            await control(first, "phase")
            await asyncio.wait_for(device_a.runner, 2)
            assert device_a.session.finalized and not device_b.session.finalized
            assert device_b.accept_audio and not device_b.stop.is_set()
            await cloud_b.send_json({"type": "session.output_audio.delta", "delta": "AQACAAMA"})
            assert (await asyncio.wait_for(second.receive(), 2)).data == b"\x01\0\x02\0\x03\0"
        finally:
            await first.close()
            await second.close()


async def test_device_backends_run_concurrently_with_separate_history_and_one_activity_poller(monkeypatch):
    accepted = asyncio.Queue()
    gates = {"native-first": asyncio.Event(), "live:second": asyncio.Event()}
    pollers, poll_stopped = [], asyncio.Event()

    async def process(request):
        assert request.headers["Authorization"] == "Bearer fake-ha-token"
        body = await request.json()
        accepted.put_nowait(body)
        identity = body["device_id"]
        await gates[identity].wait()
        return web.json_response({"conversation_id": "conversation-" + identity, "response": {
            "response_type": "action_done", "speech": {"plain": {"speech": "result-" + identity}}}})

    async def poll(backend):
        pollers.append(backend)
        try:
            await asyncio.Event().wait()
        finally:
            poll_stopped.set()

    monkeypatch.setattr(HomeAssistantBackend, "poll_activity", poll)
    ha = web.Application()
    ha.router.add_post(PROCESS_PATH, process)
    async with TestServer(ha) as server:
        overrides = {"backend": "home-assistant", "ha_url": str(server.make_url("/")),
                     "ha_token": "fake-ha-token", "ha_device_id": "native-first",
                     "room": "First test room", "devices": (SECOND,)}
        async with harness(monkeypatch, settings_overrides=overrides) as (client, cloud):
            first, cloud_a, _, device_a = await start_voice(client, cloud, TOKEN, "cloud-a")
            second, cloud_b, start_b, device_b = await start_voice(client, cloud, SECOND_TOKEN, "cloud-b")
            device_a.session.settle_seconds = device_b.session.settle_seconds = 0
            try:
                assert SECOND.room in start_b["session"]["instructions"]
                await delegate(cloud_a, "first-only-request", "first-job")
                await delegate(cloud_b, "second-only-request", "second-job")
                initial = [await asyncio.wait_for(accepted.get(), 2) for _ in range(2)]
                assert {body["device_id"] for body in initial} == set(gates)
                assert all("conversation_id" not in body for body in initial)
                by_identity = {body["device_id"]: body for body in initial}
                assert "second-only-request" not in by_identity["native-first"]["text"]
                assert "first-only-request" not in by_identity["live:second"]["text"]
                assert 'room (configured by owner): "First test room"' in by_identity["native-first"]["text"]
                assert SECOND.room in by_identity["live:second"]["text"]
                assert device_a.backend is not device_b.backend and device_a.backend.lock is not device_b.backend.lock

                gates["live:second"].set()
                await asyncio.wait_for(device_b.session.drain(), 2)
                assert device_a.session.jobs["first-job"].status == "running"
                assert device_b.session.jobs["second-job"].status == "completed"
                assert not any(socket is cloud_a and event.get("content") == "result-live:second"
                               for socket, event in cloud.received)
                gates["native-first"].set()
                await asyncio.wait_for(device_a.session.drain(), 2)
                await delegate(cloud_a, "first-followup", "first-followup-job")
                await delegate(cloud_b, "second-followup", "second-followup-job")
                followups = [await asyncio.wait_for(accepted.get(), 2) for _ in range(2)]
                assert all(body["conversation_id"] == "conversation-" + body["device_id"] for body in followups)
                await asyncio.gather(device_a.session.drain(), device_b.session.drain())
                assert len(pollers) == 1 and not poll_stopped.is_set()
            finally:
                for gate in gates.values():
                    gate.set()
                await first.close()
                await second.close()
    assert poll_stopped.is_set()


async def test_disconnected_identity_stays_claimed_until_accepted_work_drains(monkeypatch):
    accepted, release = asyncio.Event(), asyncio.Event()
    backends = []

    async def execute(*args):
        accepted.set()
        await release.wait()
        return BackendResult("old-connection-result")

    def make_backend():
        backend = SimpleNamespace(execute=AsyncMock(side_effect=execute))
        backends.append(backend)
        return backend

    monkeypatch.setattr(device_server, "DemoBackend", make_backend)
    async with harness(monkeypatch, settings_overrides={"devices": (SECOND,)}) as (client, cloud):
        first, upstream, _, old = await start_voice(client, cloud, TOKEN, "old-cloud")
        old.session.settle_seconds = 0
        other = None
        replacement = None
        try:
            await delegate(upstream, "accepted-work", "old-job")
            await asyncio.wait_for(accepted.wait(), 2)
            await first.close()
            await asyncio.wait_for(old.runner, 2)
            with pytest.raises(aiohttp.WSServerHandshakeError) as error:
                await client.ws_connect("/voice", params={"token": TOKEN})
            assert error.value.status == 409
            other = await client.ws_connect("/voice", params={"token": SECOND_TOKEN})
            health = await (await client.get("/health")).json()
            assert health["connected_devices"] == 1
            primary_hash = hashlib.sha256(b"live:primary").hexdigest()[:10]
            assert next(row for row in health["devices"] if row["device"] == primary_hash)["draining"]

            release.set()
            await asyncio.wait_for(old.session.drain(), 2)
            await until(lambda: "primary" not in client.app[device_server.REGISTRY].claims)
            replacement, new_cloud, _, new = await start_voice(client, cloud, TOKEN, "new-cloud")
            assert new is not old and new.backend is old.backend
            assert len(backends) == 2  # One persistent backend per registered identity.
            assert not new.session.jobs and new.session.conversation_id is None
            assert not any(socket is new_cloud and event.get("content") == "old-connection-result"
                           for socket, event in cloud.received)
        finally:
            release.set()
            await first.close()
            if other:
                await other.close()
            if replacement:
                await replacement.close()


async def test_failed_runner_still_drains_accepted_work_then_releases_identity(monkeypatch, caplog):
    accepted, release = asyncio.Event(), asyncio.Event()
    original_run = device_server.VoiceDevice.run_sessions

    async def fail_after_audio_close(device):
        await original_run(device)
        raise RuntimeError("PRIVATE_RUNNER_ERROR_WITH_URL_AND_TOKEN")

    async def execute(*args):
        accepted.set()
        await release.wait()
        return BackendResult("completed synthetic action")

    monkeypatch.setattr(device_server.VoiceDevice, "run_sessions", fail_after_audio_close)
    backend = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    async with harness(monkeypatch, backend) as (client, cloud):
        socket, upstream, _, device = await start_voice(client, cloud, TOKEN, "failed-runner-cloud")
        device.session.settle_seconds = 0
        replacement = None
        try:
            await delegate(upstream, "accepted synthetic request", "accepted-job")
            await asyncio.wait_for(accepted.wait(), 2)
            await socket.close()
            await until(lambda: device.runner.done())
            assert isinstance(device.runner.exception(), RuntimeError)
            assert device.session.jobs["accepted-job"].status == "running"
            with pytest.raises(aiohttp.WSServerHandshakeError) as error:
                await client.ws_connect("/voice", params={"token": TOKEN})
            assert error.value.status == 409
            assert "primary" in client.app[device_server.REGISTRY].claims

            release.set()
            await until(lambda: "primary" not in client.app[device_server.REGISTRY].claims)
            assert device.session.jobs["accepted-job"].status == "completed"
            replacement = await client.ws_connect("/voice", params={"token": TOKEN})
            backend.execute.assert_awaited_once()
            assert "exception_type=RuntimeError" in caplog.text
            assert "PRIVATE_RUNNER" not in caplog.text and TOKEN not in caplog.text
        finally:
            release.set()
            await socket.close()
            if replacement:
                await replacement.close()


async def test_cancelling_close_does_not_cancel_current_unregistered_accepted_worker():
    draining, release = asyncio.Event(), asyncio.Event()

    async def accepted_work():
        await release.wait()
        return "completed"

    worker = asyncio.create_task(accepted_work())

    async def drain():
        draining.set()
        await worker

    async def failed_runner():
        raise RuntimeError("synthetic failure before drain registration")

    device = device_server.VoiceDevice(SimpleNamespace(), None, settings(), None)
    device.session = SimpleNamespace(drain=drain)
    device.runner = asyncio.create_task(failed_runner())
    close = asyncio.create_task(device.close())
    try:
        await asyncio.wait_for(draining.wait(), 1)
        close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close
        assert not worker.done()
        release.set()
        assert await asyncio.wait_for(worker, 1) == "completed"
    finally:
        release.set()
        await asyncio.gather(close, worker, return_exceptions=True)
