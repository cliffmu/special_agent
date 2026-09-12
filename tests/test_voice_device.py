"""Voice PE wire-protocol tests with local fake device and GPT-Live sockets."""

import asyncio
import base64
import logging
import struct
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from experimental.live import device_server
from experimental.live.backend import BackendError, BackendResult


TOKEN = "test-device-token-0123456789abcdef0123456789"


class FakeCloud:
    """Preserve actual WS scheduling while tests control session readiness/audio."""

    def __init__(self):
        self.starts = asyncio.Queue()
        self.events = asyncio.Queue()
        self.connections = []
        self.devices = []
        self.received = []
        self.close_gate = None
        self.finalize = True

    async def finish(self, socket):
        if self.close_gate is not None:
            await self.close_gate.wait()
        if self.finalize:
            await socket.send_json({"type": "session.closed", "usage": {"seconds": 17}})
        await socket.close()

    async def handle(self, request):
        assert request.headers["Authorization"] == "Bearer fake-cloud-key"
        assert not request.query
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        self.connections.append(socket)
        closer = None
        try:
            async for message in socket:
                assert message.type == aiohttp.WSMsgType.TEXT
                event = message.json()
                self.received.append((socket, event))
                if event["type"] == "session.start":
                    self.starts.put_nowait((socket, event))
                else:
                    self.events.put_nowait(event)
                if event["type"] == "session.close":
                    # Keep reading while final usage is delayed, so stale microphone
                    # writes after close remain observable to the test.
                    closer = asyncio.create_task(self.finish(socket))
        finally:
            if closer:
                closer.cancel()
                await asyncio.gather(closer, return_exceptions=True)
        return socket

    async def next_event(self, kind):
        while True:
            event = await asyncio.wait_for(self.events.get(), 2)
            if event["type"] == kind:
                return event


@asynccontextmanager
async def harness(monkeypatch, backend=None, settings_overrides=None):
    cloud = FakeCloud()
    original_device = device_server.VoiceDevice

    def record_device(*args):
        device = original_device(*args)
        cloud.devices.append(device)
        return device

    monkeypatch.setattr(device_server, "VoiceDevice", record_device)
    upstream = web.Application()
    upstream.router.add_get("/v1/live/sessions", cloud.handle)
    async with TestServer(upstream) as api:
        monkeypatch.setattr(device_server, "LIVE_URL", str(api.make_url("/v1/live/sessions")))
        if backend is not None:
            monkeypatch.setattr(device_server, "DemoBackend", lambda: backend)
        settings = device_server.DeviceSettings(api_key="fake-cloud-key", device_token=TOKEN,
                                               **(settings_overrides or {}))
        async with TestClient(TestServer(device_server.create_app(settings))) as client:
            yield client, cloud


async def control(socket, expected):
    message = await asyncio.wait_for(socket.receive(), 2)
    assert message.type == aiohttp.WSMsgType.TEXT
    # The firmware's substring parser requires compact JSON, including colons.
    assert ": " not in message.data and ", " not in message.data
    event = message.json()
    assert event["type"] == expected
    return event


async def ready_device(client, cloud):
    socket = await client.ws_connect("/voice", params={"token": TOKEN})
    await socket.send_json({"type": "start"})
    hello = await control(socket, "hello")
    assert hello["follow_up_ms"] == 0 and hello["playback_prebuffer_ms"] == 80
    await socket.send_json({"type": "wake"})
    upstream, start = await asyncio.wait_for(cloud.starts.get(), 2)
    await upstream.send_json({"type": "session.started", "session": {"id": "live-device-1"}})
    await control(socket, "ack")
    assert (await control(socket, "phase"))["value"] == "listening"
    return socket, upstream, start


def test_device_server_requires_key_and_strong_token_before_listening():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        device_server.create_app(device_server.DeviceSettings(device_token=TOKEN))
    with pytest.raises(ValueError, match="VOICE_DEVICE_TOKEN"):
        device_server.create_app(device_server.DeviceSettings(api_key="fake-cloud-key", device_token="short"))


@pytest.mark.parametrize("token,origin", [("wrong", None), ("", None), (TOKEN, "https://other.invalid")])
async def test_voice_auth_rejects_invalid_token_and_browser_origins(monkeypatch, token, origin):
    async with harness(monkeypatch) as (client, cloud):
        with pytest.raises(aiohttp.WSServerHandshakeError) as error:
            await client.ws_connect("/voice", params={"token": token},
                                    headers={"Origin": origin} if origin else {})
        assert error.value.status == 401
        assert not cloud.connections
        health = await (await client.get("/health")).text()
        assert TOKEN not in health and "fake-cloud-key" not in health


async def test_wake_buffers_first_words_until_live_ready_and_transcodes_microphone(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket = await client.ws_connect("/voice", params={"token": TOKEN})
        await socket.send_bytes(b"\xff\x7f" * 32)  # Audio before wake must be discarded.
        await socket.send_json({"type": "start"})
        await control(socket, "hello")
        await socket.send_json({"type": "wake"})
        upstream, start = await asyncio.wait_for(cloud.starts.get(), 2)
        assert start["session"]["model"] == "gpt-live-1"
        assert start["session"]["audio"]["format"] == {"type": "audio/pcm", "rate": 24000}
        assert start["session"]["delegation"] == {"type": "client"}

        microphone = struct.pack("<17h", *(i * 300 for i in range(17)))
        await socket.send_bytes(microphone[:5])  # Preserve an odd sample split during startup.
        await socket.send_bytes(microphone[5:])
        # A second handshake is a receive-order barrier for preceding binary data.
        await socket.send_json({"type": "start"})
        await control(socket, "hello")
        device = cloud.devices[-1]
        assert not device.play_audio and cloud.events.empty()

        await upstream.send_json({"type": "session.started", "session": {"id": "live-device-1"}})
        await control(socket, "ack")
        assert (await control(socket, "phase"))["value"] == "listening"
        assert device.session.live_id == "live-device-1"
        # 16 kHz ramp: interpolation at 24 kHz produces increments of 200.
        pcm = b""
        while len(pcm) < 50:
            event = await cloud.next_event("session.input_audio.append")
            pcm += base64.b64decode(event["audio"], validate=True)
        assert pcm == struct.pack("<25h", *(i * 200 for i in range(25)))

        output = struct.pack("<3000h", *(i - 1500 for i in range(3000)))
        await upstream.send_json({"type": "session.output_audio.delta",
                                  "delta": base64.b64encode(output).decode("ascii")})
        played = b""
        while len(played) < len(output):
            message = await asyncio.wait_for(socket.receive(), 2)
            assert message.type == aiohttp.WSMsgType.BINARY
            assert len(message.data) <= 4096
            played += message.data
        assert played == output  # Output remains the negotiated 24 kHz bytes.
        await socket.close()
        await asyncio.wait_for(device.runner, 2)
        assert device.session.finalized and device.session.usage["seconds"] == 17


async def test_startup_buffers_one_hundred_firmware_frames_before_live_is_ready(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket = await client.ws_connect("/voice", params={"token": TOKEN})
        await socket.send_json({"type": "wake"})
        upstream, _ = await asyncio.wait_for(cloud.starts.get(), 2)
        # The firmware sends 512 bytes every 16 ms. This is 1.6 s, below the
        # intended 2 s byte bound but above the old 64-packet/1.024 s limit.
        for _ in range(100):
            await socket.send_bytes(b"\0" * 512)
        await socket.send_json({"type": "start"})
        await control(socket, "hello")
        device = cloud.devices[-1]
        assert device.mic_bytes == 51200 and device.mic.qsize() == 100
        assert device.accept_audio and not device.stop.is_set()

        await upstream.send_json({"type": "session.started", "session": {"id": "live-buffered"}})
        await control(socket, "ack")
        assert (await control(socket, "phase"))["value"] == "listening"
        # Preserve every buffered input sample through the stateful conversion.
        expected_bytes = ((100 * 256 - 1) * 3 // 2 + 1) * 2
        received = 0
        while received < expected_bytes:
            event = await cloud.next_event("session.input_audio.append")
            pcm = base64.b64decode(event["audio"], validate=True)
            assert not any(pcm)
            received += len(pcm)
        assert received == expected_bytes
        assert device.accept_audio and device.last_stop_reason is None
        await socket.close()


async def test_startup_still_stops_when_pcm_exceeds_two_second_byte_bound(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket = await client.ws_connect("/voice", params={"token": TOKEN})
        await socket.send_json({"type": "wake"})
        await asyncio.wait_for(cloud.starts.get(), 2)
        for _ in range(125):
            await socket.send_bytes(b"\0" * 512)
        await socket.send_json({"type": "start"})
        await control(socket, "hello")
        device = cloud.devices[-1]
        assert device.mic_bytes == 64000 and device.mic.qsize() == 125
        assert not device.stop.is_set()

        await socket.send_bytes(b"\0" * 512)
        await asyncio.wait_for(asyncio.shield(device.runner), 2)
        assert device.last_stop_reason == "mic_backlog"
        assert device.mic_bytes == 64000 and not device.accept_audio
        assert device.session.finalized
        await socket.close()


@pytest.mark.parametrize("stop_event", ["button_cancel", "interrupt"])
async def test_pcm_keeps_flowing_while_backend_waits_and_stop_preserves_accepted_work(
    monkeypatch, stop_event
):
    running, release = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        running.set()
        await release.wait()
        return BackendResult("The accepted task finished")

    backend = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    async with harness(monkeypatch, backend) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        device.session.settle_seconds = 0
        try:
            await upstream.send_json({"type": "session.input_transcript.delta", "delta": "Run a task"})
            await upstream.send_json({"type": "session.delegation.created",
                                      "delegation": {"id": "job-1", "target": "client"}})
            await asyncio.wait_for(running.wait(), 2)
            await socket.send_bytes(struct.pack("<3h", 0, 300, 600))
            microphone = await cloud.next_event("session.input_audio.append")
            assert base64.b64decode(microphone["audio"]) == struct.pack("<4h", 0, 200, 400, 600)
            await upstream.send_json({"type": "session.output_audio.delta", "delta": "AQACAAMA"})
            audio = await asyncio.wait_for(socket.receive(), 2)
            assert audio.type == aiohttp.WSMsgType.BINARY and audio.data == b"\x01\0\x02\0\x03\0"
            assert device.session.jobs["job-1"].status == "running"
            await socket.send_json({"type": stop_event})
            assert (await control(socket, "phase"))["value"] == "idle"
            await cloud.next_event("session.close")
            await asyncio.wait_for(device.runner, 2)
            assert device.session.finalized and device.session.usage["seconds"] == 17
            assert device.last_stop_reason == "device_control_" + stop_event
            health = await (await client.get("/health")).json()
            assert health["last_stop_reason"] == device.last_stop_reason
            assert device.session.jobs["job-1"].status == "running"
            release.set()
            await asyncio.wait_for(device.session.drain(), 2)
            assert device.session.jobs["job-1"].status == "completed"
            backend.execute.assert_awaited_once()
        finally:
            release.set()
            await socket.close()


async def test_interrupt_followed_immediately_by_wake_restarts_after_finalization(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket, _, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        previous = device.session
        await socket.send_json({"type": "interrupt"})
        await socket.send_json({"type": "wake"})
        upstream, _ = await asyncio.wait_for(cloud.starts.get(), 2)
        assert previous.finalized
        await upstream.send_json({"type": "session.started", "session": {"id": "live-device-2"}})
        await control(socket, "ack")
        assert (await control(socket, "phase"))["value"] == "listening"
        assert device.session is not previous and device.session.live_id == "live-device-2"
        await socket.close()
        await asyncio.wait_for(device.runner, 2)


async def test_restart_preserves_new_microphone_while_previous_usage_is_pending(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket, previous_cloud, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        previous = device.session
        cloud.close_gate = asyncio.Event()
        await socket.send_json({"type": "interrupt"})
        assert (await control(socket, "phase"))["value"] == "idle"
        await cloud.next_event("session.close")
        await socket.send_json({"type": "wake"})
        await socket.send_bytes(struct.pack("<3h", 0, 300, 600))
        await socket.send_json({"type": "start"})
        await control(socket, "hello")
        assert not previous.finalized and cloud.starts.empty()
        assert device.mic_bytes == 6

        cloud.close_gate.set()
        upstream, _ = await asyncio.wait_for(cloud.starts.get(), 2)
        assert previous.finalized
        await upstream.send_json({"type": "session.started", "session": {"id": "live-device-2"}})
        await control(socket, "ack")
        assert (await control(socket, "phase"))["value"] == "listening"
        microphone = await cloud.next_event("session.input_audio.append")
        assert base64.b64decode(microphone["audio"]) == struct.pack("<4h", 0, 200, 400, 600)
        assert not any(connection is previous_cloud and event["type"] == "session.input_audio.append"
                       for connection, event in cloud.received)
        await socket.close()
        await asyncio.wait_for(device.runner, 2)


async def test_missing_final_usage_prevents_queued_automatic_restart(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket, _, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        previous = device.session
        cloud.close_gate = asyncio.Event()
        cloud.finalize = False
        original_close = previous.close

        async def close_with_short_deadline(timeout=12):
            await original_close(timeout=0.1)
            cloud.close_gate.set()

        monkeypatch.setattr(previous, "close", close_with_short_deadline)
        await socket.send_json({"type": "interrupt"})
        await control(socket, "phase")
        await cloud.next_event("session.close")
        await socket.send_json({"type": "wake"})
        await socket.send_bytes(struct.pack("<3h", 0, 300, 600))
        await asyncio.wait_for(device.runner, 2)
        assert not previous.finalized and device.session is previous
        assert not device.restart and not device.accept_audio
        assert cloud.starts.empty() and len(cloud.connections) == 1
        await socket.close()


@pytest.mark.parametrize("fast_mode", [False, True])
async def test_hardware_room_reaches_ha_without_triggering_satellite_tts(monkeypatch, fast_mode):
    requests = asyncio.Queue()

    async def converse(request):
        assert request.headers["Authorization"] == "Bearer fake-ha-token"
        requests.put_nowait(await request.json())
        return web.json_response({"conversation_id": "ha-room-1", "response": {
            "response_type": "action_done", "speech": {"plain": {"speech": "The lights are on"}}}})

    ha = web.Application()
    ha.router.add_post("/api/special_agent/live/process", converse)
    async with TestServer(ha) as server:
        settings = {"backend": "home-assistant", "ha_url": str(server.make_url("/")),
                    "ha_token": "fake-ha-token", "room": "Kitchen", "agent_model": "gpt-5.6-terra",
                    "reasoning_effort": "low", "fast_mode": fast_mode}
        async with harness(monkeypatch, settings_overrides=settings) as (client, cloud):
            socket, upstream, start = await ready_device(client, cloud)
            device = cloud.devices[-1]
            device.session.settle_seconds = 0
            assert "Kitchen" in start["session"]["instructions"]
            assert start["session"]["model"] == "gpt-live-1"
            await upstream.send_json({"type": "session.input_transcript.delta", "delta": "Turn on the lights here"})
            await upstream.send_json({"type": "session.delegation.created",
                                      "delegation": {"id": "room-job", "target": "client"}})
            body = await asyncio.wait_for(requests.get(), 2)
            assert body["agent_id"] == "conversation.special_agent"
            assert body["model"] == "gpt-5.6-terra"
            assert body["reasoning_effort"] == "low"
            assert body["fast_mode"] is fast_mode
            assert 'Voice device room (configured by owner): "Kitchen"' in body["text"]
            assert "Current request: Turn on the lights here" in body["text"]
            assert body["device_id"] == "live:primary"
            await asyncio.wait_for(device.session.drain(), 2)
            assert device.session.conversation_id == "ha-room-1"
            await socket.close()
            await asyncio.wait_for(device.runner, 2)


@pytest.mark.parametrize("outcome", ["success", "semantic_error", "http_error"])
async def test_live_job_and_ha_activity_share_safe_ids_and_report_outcomes(monkeypatch, caplog, outcome):
    caplog.set_level(logging.INFO)
    requests = asyncio.Queue()

    async def converse(request):
        requests.put_nowait(await request.json())
        if outcome == "http_error":
            return web.Response(status=503, text="PRIVATE_HTTP_BODY")
        return web.json_response({"conversation_id": "PRIVATE_CONVERSATION_ID", "response": {
            "response_type": "error" if outcome == "semantic_error" else "action_done",
            "speech": {"plain": {"speech": "PRIVATE_HA_RESULT"}}}})

    ha = web.Application()
    ha.router.add_post("/api/special_agent/live/process", converse)
    async with TestServer(ha) as server:
        settings = {"backend": "home-assistant", "ha_url": str(server.make_url("/")),
                    "ha_token": "PRIVATE_HA_TOKEN", "room": "PRIVATE_ROOM"}
        async with harness(monkeypatch, settings_overrides=settings) as (client, cloud):
            socket, upstream, _ = await ready_device(client, cloud)
            device = cloud.devices[-1]
            device.session.settle_seconds = 0
            await upstream.send_json({"type": "session.input_transcript.delta", "delta": "PRIVATE_REQUEST_TEXT"})
            await upstream.send_json({"type": "session.delegation.created",
                                      "delegation": {"id": "PRIVATE_JOB_ID", "target": "client"}})
            await asyncio.wait_for(requests.get(), 2)
            await asyncio.wait_for(device.session.drain(), 2)
            events = [dict(field.split("=", 1) for field in record.getMessage().split())
                      for record in caplog.records if record.name.startswith("experimental.live.")
                      and record.name.endswith(".activity")]
            delegation = [event for event in events if event["event"] == "delegation"]
            ha_events = [event for event in events if event["event"] == "ha_request"]
            assert [event["phase"] for event in delegation] == ["queued", "started", "finished"]
            assert [event["phase"] for event in ha_events] == ["sent", "received"]
            assert len({(event["session"], event["job"]) for event in events}) == 1
            assert ha_events[-1]["status"] == ("completed" if outcome == "success" else "error")
            assert ha_events[-1]["http_status"] == ("503" if outcome == "http_error" else "200")
            assert delegation[-1]["status"] == ("completed" if outcome == "success" else "failed")
            assert int(ha_events[-1]["elapsed_ms"]) >= 0 and int(delegation[-1]["elapsed_ms"]) >= 0
            assert "PRIVATE" not in caplog.text and TOKEN not in caplog.text
            assert "fake-cloud-key" not in caplog.text
            await socket.close()
            await asyncio.wait_for(device.runner, 2)


@pytest.mark.parametrize("finish", ["interrupt", "cloud_closed"])
async def test_blocked_speaker_does_not_block_cloud_events_or_replay_after_stop(monkeypatch, finish):
    backend_started, release_backend = asyncio.Event(), asyncio.Event()
    write_started, release_write, write_cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    delivered = []

    async def execute(*args):
        backend_started.set()
        await release_backend.wait()
        return BackendResult("The task finished")

    backend = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    async with harness(monkeypatch, backend) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        device.session.settle_seconds = 0
        original_write = device.socket.send_bytes

        async def blocked_write(pcm):
            write_started.set()
            try:
                await release_write.wait()
            except asyncio.CancelledError:
                write_cancelled.set()
                raise
            delivered.append(pcm)
            await original_write(pcm)

        slow_write = AsyncMock(side_effect=blocked_write)
        monkeypatch.setattr(device.socket, "send_bytes", slow_write)
        try:
            # Several queued speaker chunks, with the very first write stalled.
            await upstream.send_json({"type": "session.output_audio.delta",
                                      "delta": base64.b64encode(b"\x01\0" * 6000).decode("ascii")})
            await asyncio.wait_for(write_started.wait(), 2)
            await upstream.send_json({"type": "session.input_transcript.delta", "delta": "Run a task"})
            await upstream.send_json({"type": "session.usage.updated", "usage": {"seconds": 12}})
            await upstream.send_json({"type": "session.delegation.created",
                                      "delegation": {"id": "slow-speaker-job", "target": "client"}})
            await asyncio.wait_for(backend_started.wait(), 2)
            assert device.session.transcripts[-1]["text"] == "Run a task"
            assert device.session.usage["seconds"] == 12
            assert not release_write.is_set() and not write_cancelled.is_set()

            if finish == "interrupt":
                await socket.send_json({"type": "interrupt"})
            else:
                # A terminal cloud event must also get past the blocked speaker.
                await upstream.send_json({"type": "session.closed", "usage": {"seconds": 17}})
            assert (await control(socket, "phase"))["value"] == "idle"
            await asyncio.wait_for(device.runner, 2)
            assert device.session.finalized and device.session.usage["seconds"] == 17
            assert write_cancelled.is_set()
            release_write.set()
            # The device remains connected; stale audio must not precede this reply.
            await socket.send_json({"type": "start"})
            await control(socket, "hello")
            assert delivered == [] and slow_write.await_count == 1
            release_backend.set()
            await asyncio.wait_for(device.session.drain(), 2)
        finally:
            release_write.set()
            release_backend.set()
            await socket.close()


async def test_repeated_wake_acknowledges_watchdog_without_restarting_cloud_or_context(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        previous = device.session
        previous.conversation_id = "ha-existing-context"
        await upstream.send_json({"type": "session.input_transcript.delta", "delta": "Tell me a story"})
        await socket.send_json({"type": "wake"})
        await control(socket, "ack")
        await socket.send_bytes(struct.pack("<3h", 0, 300, 600))
        await cloud.next_event("session.input_audio.append")
        assert device.session is previous and previous.conversation_id == "ha-existing-context"
        assert len(cloud.connections) == 1 and cloud.starts.empty()
        assert not device.stop.is_set() and not device.restart
        assert not any(event["type"] == "session.close" for _, event in cloud.received)
        await socket.close()


async def test_repeated_wake_during_connecting_does_not_ack_before_cloud_ready(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket = await client.ws_connect("/voice", params={"token": TOKEN})
        await socket.send_json({"type": "wake"})
        upstream, _ = await asyncio.wait_for(cloud.starts.get(), 2)
        await socket.send_json({"type": "wake"})
        await socket.send_json({"type": "start"})
        # Handshake response is an ordering barrier; a premature ack would be first.
        await control(socket, "hello")
        assert len(cloud.connections) == 1
        await upstream.send_json({"type": "session.started", "session": {"id": "live-ready"}})
        await control(socket, "ack")
        await control(socket, "phase")
        await socket.close()


async def test_spoken_correction_during_story_keeps_same_session_open(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        previous = device.session
        await upstream.send_json({"type": "session.output_transcript.delta", "delta": "Once upon a time"})
        await upstream.send_json({"type": "session.output_audio.delta", "delta": "AQACAAMA"})
        assert (await asyncio.wait_for(socket.receive(), 2)).type == aiohttp.WSMsgType.BINARY
        # Speech alone contains no terminal device-control message.
        await socket.send_bytes(struct.pack("<3h", 0, 300, 600))
        await upstream.send_json({"type": "session.input_transcript.delta", "delta": "Make the story more interesting"})
        await cloud.next_event("session.input_audio.append")
        await upstream.send_json({"type": "session.output_audio.delta", "delta": "BAAFAA=="})
        assert (await asyncio.wait_for(socket.receive(), 2)).data == b"\x04\0\x05\0"
        assert device.session is previous and device.accept_audio and not device.stop.is_set()
        assert device.last_stop_reason is None
        assert not any(event["type"] == "session.close" for _, event in cloud.received)
        await socket.close()


async def test_microphone_starvation_records_reason_and_task_exception_type(monkeypatch, caplog):
    async with harness(monkeypatch) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        # Playback alone must not hide a missing microphone transport.
        await upstream.send_json({"type": "session.output_audio.delta", "delta": "AQACAAMA"})
        await socket.receive()
        await asyncio.wait_for(asyncio.shield(device.runner), 4.5)
        assert device.last_stop_reason == "mic_starvation"
        assert "task=microphone exception_type=TimeoutError" in caplog.text
        health = await (await client.get("/health")).json()
        assert health["last_stop_reason"] == "mic_starvation"
        assert not health["audio_active"] and health["device_connected"]
        await socket.close()


async def test_speaker_burst_records_backlog_without_misreporting_cloud_close(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        # One large delta overflows the 16-slot queue before the writer can run.
        await upstream.send_json({"type": "session.output_audio.delta",
                                  "delta": base64.b64encode(b"\x01\0" * (4096 * 17 // 2)).decode("ascii")})
        await asyncio.wait_for(asyncio.shield(device.runner), 2)
        assert device.last_stop_reason == "speaker_backlog"
        await socket.close()


async def test_speaker_failure_logs_exception_type_without_private_error_text(monkeypatch, caplog):
    async with harness(monkeypatch) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        monkeypatch.setattr(device.socket, "send_bytes", AsyncMock(side_effect=OSError("PRIVATE_AUDIO_URL_OR_TOKEN")))
        await upstream.send_json({"type": "session.output_audio.delta", "delta": "AQACAAMA"})
        await asyncio.wait_for(asyncio.shield(device.runner), 2)
        assert device.last_stop_reason == "speaker_write_error"
        assert "task=speaker exception_type=OSError" in caplog.text
        assert "PRIVATE_AUDIO_URL_OR_TOKEN" not in caplog.text
        await socket.close()


@pytest.mark.parametrize("reason", ["idle_timeout", "max_duration"])
async def test_session_deadline_has_specific_reason(monkeypatch, reason):
    async with harness(monkeypatch, settings_overrides={"max_duration": 30}) as (client, cloud):
        socket, _, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        if reason == "idle_timeout":
            device.session.last_activity -= device.settings.idle_timeout + 1
        else:
            device.session.created_at -= device.settings.max_duration + 1
        await asyncio.wait_for(asyncio.shield(device.runner), 2)
        assert device.last_stop_reason == reason
        assert device.session.finalized
        await socket.close()


async def test_disabled_duration_cap_keeps_active_conversation_past_ten_minutes(monkeypatch):
    async with harness(monkeypatch, settings_overrides={"max_duration": 0}) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        device.session.created_at -= 601
        await upstream.send_json({"type": "session.input_transcript.delta", "delta": "Keep going"})
        await asyncio.sleep(0.35)
        assert device.accept_audio and not device.stop.is_set()
        assert device.last_stop_reason is None
        await socket.close()


@pytest.mark.parametrize("output_level", [0, 32])
async def test_silent_output_and_untranscribed_microphone_noise_do_not_reset_idle(monkeypatch, output_level):
    async with harness(monkeypatch) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        device.session.last_activity -= device.settings.idle_timeout + 1
        # Room noise/silence streams continuously; only recognized user speech counts.
        await socket.send_bytes(struct.pack("<256h", *([1000] * 256)))
        await upstream.send_json({"type": "session.output_audio.delta",
                                  "delta": base64.b64encode(struct.pack("<256h", *([output_level] * 256))).decode("ascii")})
        await cloud.next_event("session.input_audio.append")
        await asyncio.wait_for(asyncio.shield(device.runner), 2)
        assert device.last_stop_reason == "idle_timeout"
        await socket.close()


async def test_audible_output_without_transcript_resets_idle_and_tracks_playback_end(monkeypatch):
    async with harness(monkeypatch) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        device.session.last_activity -= device.settings.idle_timeout + 1
        audio = struct.pack("<2400h", *([1000] * 2400))  # 100 ms at 24 kHz.
        await upstream.send_json({"type": "session.output_audio.delta",
                                  "delta": base64.b64encode(audio).decode("ascii")})
        played = b""
        while len(played) < len(audio):
            played += (await asyncio.wait_for(socket.receive(), 2)).data
        await asyncio.sleep(0.35)
        assert device.accept_audio and not device.stop.is_set()
        assert device.session.last_activity == device.session.playback_until
        assert device.session.last_activity > device.session.created_at
        await socket.close()


@pytest.mark.parametrize("failed", [False, True])
async def test_long_backend_work_and_completion_get_full_idle_grace(monkeypatch, failed):
    running, release = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        running.set()
        await release.wait()
        if failed:
            raise BackendError("No matching device")
        return BackendResult("The work finished")

    backend = SimpleNamespace(execute=execute)
    async with harness(monkeypatch, backend) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        device.session.settle_seconds = 0
        try:
            await upstream.send_json({"type": "session.input_transcript.delta", "delta": "Do the work"})
            await upstream.send_json({"type": "session.delegation.created",
                                      "delegation": {"id": "long-job", "target": "client"}})
            await asyncio.wait_for(running.wait(), 2)
            device.session.last_activity -= device.settings.idle_timeout + 100
            await asyncio.sleep(0.35)
            assert not device.stop.is_set()
            release.set()
            await asyncio.wait_for(device.session.drain(), 2)
            assert device.session.jobs["long-job"].status == ("failed" if failed else "completed")
            await asyncio.sleep(0.35)
            assert device.accept_audio and not device.stop.is_set()
            device.session.last_activity -= device.settings.idle_timeout + 1
            await asyncio.wait_for(asyncio.shield(device.runner), 2)
            assert device.last_stop_reason == "idle_timeout"
        finally:
            release.set()
            await socket.close()


@pytest.mark.parametrize("code,logged_code", [
    ("credit_balance_exhausted", "credit_balance_exhausted"),
    ("private_token_could_be_in_unknown_code", "unknown"),
])
async def test_live_error_logs_known_machine_code_without_secret_message(monkeypatch, caplog, code, logged_code):
    async with harness(monkeypatch) as (client, cloud):
        socket, upstream, _ = await ready_device(client, cloud)
        device = cloud.devices[-1]
        await upstream.send_json({"type": "error", "error": {
            "code": code, "message": "PRIVATE_API_KEY_URL_OR_TRANSCRIPT", "param": "PRIVATE_PARAM",
        }})
        await asyncio.wait_for(asyncio.shield(device.runner), 2)
        assert device.last_stop_reason == "cloud_error"
        assert "Live error code: " + logged_code in caplog.text
        assert "PRIVATE_API_KEY_URL_OR_TRANSCRIPT" not in caplog.text
        assert "PRIVATE_PARAM" not in caplog.text
        if logged_code == "unknown":
            assert code not in caplog.text
        await socket.close()
