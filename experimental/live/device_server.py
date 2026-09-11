"""Home Assistant Voice PE PCM bridge: python -m experimental.live.device_server."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field

import aiohttp
from aiohttp import web

from .audio import AudioPacer, PCM16Resampler
from .backend import DemoBackend, HomeAssistantBackend
from .server import Settings, voice_instructions
from .session import LiveSession

LOG = logging.getLogger(__name__)
LIVE_URL = "wss://api.openai.com/v1/live/sessions"
# Allow ordinary 512-byte firmware frames to reach the 64,000-byte (2 s) bound.
# A separate packet limit still bounds queue bookkeeping for tiny fragments.
MIC_QUEUE_PACKETS = 256
SAFE_LIVE_ERROR_CODES = frozenset({
    "authentication_error", "credit_balance_exhausted", "insufficient_quota",
    "invalid_api_key", "invalid_request_error", "model_not_found",
    "permission_denied", "rate_limit_exceeded", "server_error",
})


@dataclass
class DeviceSettings(Settings):
    device_token: str = field(default="", repr=False)
    room: str = ""
    bind: str = "0.0.0.0"

    def validate(self):
        super().validate()
        if len(self.device_token) < 32 or not self.device_token.isascii():
            raise ValueError("Set VOICE_DEVICE_TOKEN to a random ASCII token of at least 32 characters")
        if not self.api_key:
            raise ValueError("Set OPENAI_API_KEY; demo mode still uses GPT-Live for voice")
        if len(self.room) > 120:
            raise ValueError("VOICE_ROOM must be a short room name")


class VoiceDevice:
    """One authenticated satellite; capture, playback and HA jobs are independent."""

    def __init__(self, socket, http, settings, backend):
        self.socket, self.http, self.settings, self.backend = socket, http, settings, backend
        self.session = None
        self.runner = None
        self.stop = asyncio.Event()
        self.mic = asyncio.Queue(maxsize=MIC_QUEUE_PACKETS)
        self.mic_bytes = 0
        self.accept_audio = False
        self.play_audio = False
        self.completed = set()
        self.restart = False
        self.closing = False
        self.stop_reason = None
        self.last_stop_reason = None

    def record_stop(self, reason):
        """Keep the first fixed reason code; never include device or API payloads."""
        if self.stop_reason is None:
            self.stop_reason = self.last_stop_reason = reason
            LOG.info("Voice session stopping: reason=%s", reason)

    def stop_for(self, reason):
        self.record_stop(reason)
        self.stop.set()

    def inspect_tasks(self, tasks):
        """Observe failures without changing which completion ends the session."""
        for task in tasks:
            if task.done() and not task.cancelled():
                error = task.exception()
                if error is not None:
                    self.record_stop({"cloud_reader": "cloud_read_error", "microphone": "mic_task_error",
                                      "speaker": "speaker_write_error", "limits": "limits_error"}.get(
                                          task.get_name(), "session_task_error"))
                    LOG.warning("Voice task failed: task=%s exception_type=%s",
                                task.get_name(), type(error).__name__)

    async def control(self, kind, **values):
        # The pinned firmware parses compact JSON with substring comparisons.
        await asyncio.wait_for(self.socket.send_str(json.dumps(
            {"type": kind, **values}, separators=(",", ":"))), 2)

    async def receive(self, message):
        if message.type == aiohttp.WSMsgType.BINARY:
            if self.accept_audio:
                if len(message.data) > 8192 or self.mic.full() or self.mic_bytes + len(message.data) > 64000:
                    # Never build an unbounded delayed recording or replay stale microphone data.
                    self.stop_for("mic_packet_oversized" if len(message.data) > 8192 else "mic_backlog")
                    self.restart = self.accept_audio = False
                else:
                    self.mic.put_nowait((time.monotonic(), message.data))
                    self.mic_bytes += len(message.data)
            return
        if message.type != aiohttp.WSMsgType.TEXT:
            return
        event = message.json()
        if not isinstance(event, dict):
            raise ValueError("Expected a device event")
        kind = event.get("type")
        if kind == "start":
            await self.control("hello", follow_up_ms=0, wake_open_delay_ms=400, playback_prebuffer_ms=80)
        elif kind == "wake" and not self.closing:
            if self.runner is None or self.runner.done():
                self.stop = asyncio.Event()
                self.stop_reason = None
                self.mic = asyncio.Queue(maxsize=MIC_QUEUE_PACKETS)
                self.mic_bytes = 0
                self.accept_audio = True
                self.runner = asyncio.create_task(self.run_sessions())
            elif self.stop.is_set():
                # Firmware emits interrupt then wake when starting over during playback.
                self.restart = True
                self.mic = asyncio.Queue(maxsize=MIC_QUEUE_PACKETS)
                self.mic_bytes = 0
                self.accept_audio = True
            elif self.accept_audio and self.session and self.session.state == "active":
                # A repeated wake starts the firmware's no-speech watchdog even
                # while Live is active. Acknowledge it without losing context.
                await self.control("ack")
        elif kind in ("interrupt", "button_cancel", "flush", "false_flag"):
            self.accept_audio = False
            self.play_audio = False
            self.restart = False
            self.stop_for("device_control_" + kind)

    async def run_sessions(self):
        while not self.closing:
            await self.run_session()
            if self.session and not self.session.finalized:
                self.restart = self.accept_audio = False
                if not self.socket.closed:
                    await self.control("phase", value="idle")
                return
            if not self.restart or self.closing or self.socket.closed:
                return
            self.restart = False
            self.stop = asyncio.Event()
            self.stop_reason = None
            self.accept_audio = True

    async def run_session(self):
        session, cloud, tasks = None, None, []
        microphone, stop = self.mic, self.stop
        speaker = asyncio.Queue(maxsize=16)
        stage = "cloud_connect"
        try:
            cloud = await asyncio.wait_for(self.http.ws_connect(
                LIVE_URL, headers={"Authorization": f"Bearer {self.settings.api_key}"},
                heartbeat=20, max_msg_size=2**20), 15)
            send_lock = asyncio.Lock()

            async def send(event):
                async with send_lock:
                    try:
                        await asyncio.wait_for(cloud.send_json(event), 5)
                    except asyncio.TimeoutError:
                        self.record_stop("cloud_write_timeout")
                        raise

            session = self.session = LiveSession("pending", send, self.backend)
            started = asyncio.Event()

            async def read_cloud():
                async for message in cloud:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        event = message.json()
                        if not isinstance(event, dict):
                            raise ValueError("Expected a Live event")
                        kind = event.get("type")
                        if kind == "session.output_audio.delta":
                            if session.state == "active" and not stop.is_set():
                                pcm = base64.b64decode(event["delta"], validate=True)
                                if len(pcm) % 2:
                                    raise ValueError("Unaligned Live PCM")
                                # Do not block cloud control/delegation events on satellite Wi-Fi.
                                for offset in range(0, len(pcm), 4096):
                                    if speaker.full():
                                        self.stop_for("speaker_backlog")
                                        return
                                    speaker.put_nowait((time.monotonic(), pcm[offset:offset+4096]))
                        else:
                            await session.receive(event)
                            if kind == "session.started":
                                session.live_id = event.get("session", {}).get("id", "unknown")
                                started.set()
                            elif kind == "error":
                                self.record_stop("cloud_error")
                                detail = event.get("error")
                                code = detail.get("code") if isinstance(detail, dict) else None
                                safe_code = code if isinstance(code, str) and code in SAFE_LIVE_ERROR_CODES else "unknown"
                                LOG.warning("Live error code: %s", safe_code)
                            elif kind == "session.closed":
                                self.record_stop("cloud_closed")
                        if session.finalized or session.state == "error":
                            if session.state == "error":
                                self.record_stop("session_error")
                            return
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        self.record_stop("cloud_disconnected")
                        return
                self.record_stop("cloud_disconnected")

            reader = asyncio.create_task(read_cloud(), name="cloud_reader")
            tasks.append(reader)
            stage = "cloud_start"
            await send({"type": "session.start", "event_id": uuid.uuid4().hex, "session": {
                "model": "gpt-live-1", "instructions": voice_instructions(self.settings.backend, self.settings.room),
                "audio": {"format": {"type": "audio/pcm", "rate": 24000}, "output": {"voice": "marin"}},
                "delegation": {"type": "client"}}})
            ready = asyncio.create_task(started.wait(), name="cloud_ready")
            stopped = asyncio.create_task(self.stop.wait(), name="stop_waiter")
            tasks.extend((ready, stopped))
            done, _ = await asyncio.wait((ready, reader, stopped), timeout=15, return_when=asyncio.FIRST_COMPLETED)
            self.inspect_tasks(done)
            if not done:
                self.record_stop("cloud_start_timeout")
            if not started.is_set() or reader.done() or self.stop.is_set():
                return
            self.accept_audio = True
            self.play_audio = True
            stage = "device_control"
            await self.control("ack")
            # Never send replying/thinking/listening per utterance: upstream phase changes
            # can gate capture or flush playback. The device stays duplex until session end.
            await self.control("phase", value="listening")

            async def send_microphone():
                converter = PCM16Resampler()
                pacer = AudioPacer()
                while True:
                    try:
                        received_at, pcm = await asyncio.wait_for(microphone.get(), 3)
                    except asyncio.TimeoutError:
                        self.record_stop("mic_starvation")
                        raise
                    if stop.is_set():
                        return
                    if microphone is self.mic:
                        self.mic_bytes -= len(pcm)
                    if time.monotonic() - received_at > 2:
                        self.stop_for("mic_stale")
                        return
                    for chunk in converter.feed(pcm):
                        await pacer.wait_for_chunk(len(chunk))
                        if stop.is_set():
                            return
                        await send({"type": "session.input_audio.append",
                                    "audio": base64.b64encode(chunk).decode("ascii")})

            async def play_speaker():
                while not stop.is_set():
                    received_at, pcm = await speaker.get()
                    if stop.is_set() or not self.play_audio:
                        return
                    if time.monotonic() - received_at > 0.5:
                        self.stop_for("speaker_stale")
                        return
                    try:
                        await asyncio.wait_for(self.socket.send_bytes(pcm), 0.5)
                    except asyncio.TimeoutError:
                        self.record_stop("speaker_write_timeout")
                        raise

            async def limits():
                while not self.stop.is_set():
                    await asyncio.sleep(0.25)
                    now = time.monotonic()
                    busy = any(j.status in ("waiting_for_context", "running") for j in session.jobs.values())
                    if session.state == "error":
                        self.stop_for("session_error")
                    elif now-session.created_at >= self.settings.max_duration:
                        self.stop_for("max_duration")
                    elif not busy and now-session.last_activity >= self.settings.idle_timeout:
                        self.stop_for("idle_timeout")

            stage = "streaming"
            pump = asyncio.create_task(send_microphone(), name="microphone")
            playback = asyncio.create_task(play_speaker(), name="speaker")
            monitor = asyncio.create_task(limits(), name="limits")
            tasks.extend((pump, playback, monitor))
            done, _ = await asyncio.wait((reader, stopped, pump, playback, monitor), return_when=asyncio.FIRST_COMPLETED)
            self.inspect_tasks(done)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, binascii.Error) as error:
            # Do not log connection URLs (device token), audio or private transcripts.
            self.record_stop(stage + ("_timeout" if isinstance(error, asyncio.TimeoutError) else "_error"))
            LOG.warning("Voice transport failed: stage=%s exception_type=%s", stage, type(error).__name__)
        finally:
            self.record_stop("stop_requested")
            self.accept_audio = self.restart and not self.closing
            self.play_audio = False
            # Stop all input producers before graceful close. Only the cloud reader
            # remains alive to collect session.closed; a new wake owns a new PCM queue.
            for task in tasks[1:]:
                task.cancel()
            await asyncio.gather(*tasks[1:], return_exceptions=True)
            if not self.socket.closed and not self.restart:
                try:
                    await self.control("phase", value="idle")
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
            if session and not session.finalized:
                await session.close(timeout=12 if tasks and not tasks[0].done() else 0.1)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if cloud:
                await cloud.close()
            if session:
                LOG.info("Voice session ended: reason=%s finalized=%s seconds=%s tasks=%s",
                         self.stop_reason, session.finalized, session.usage.get("seconds", "unknown"),
                         [j.status for j in session.jobs.values()])
                # Audio closure does not cancel a home action already accepted by HA.
                drain = asyncio.create_task(session.drain())
                self.completed.add(drain)
                drain.add_done_callback(self.completed.discard)

    async def close(self):
        self.closing = True
        self.restart = False
        self.accept_audio = False
        self.stop_for("device_connection_closed")
        if self.runner:
            await asyncio.shield(self.runner)
        await asyncio.gather(*self.completed, return_exceptions=True)


HTTP = web.AppKey("http", aiohttp.ClientSession)
BACKEND = web.AppKey("backend", object)


def create_app(settings):
    settings.validate()
    app = web.Application(client_max_size=16384)
    connections = set()
    connect_lock = asyncio.Lock()
    last_stop_reason = None

    async def resources(app):
        async with aiohttp.ClientSession() as http:
            app[HTTP] = http
            app[BACKEND] = DemoBackend() if settings.backend == "demo" else HomeAssistantBackend(
                http, settings.ha_url, settings.ha_token, settings.ha_agent_id, settings.room)
            yield

    async def shutdown(app):
        async def close(device):
            await device.socket.close()
            await device.close()
        await asyncio.gather(*(close(device) for device in list(connections)), return_exceptions=True)

    async def voice(request):
        nonlocal last_stop_reason
        supplied = request.query.get("token", "")
        if request.headers.get("Origin") or not hmac.compare_digest(
                supplied.encode("utf-8"), settings.device_token.encode("ascii")):
            raise web.HTTPUnauthorized(text="Device authentication required")
        async with connect_lock:
            if any(not device.socket.closed for device in connections):
                raise web.HTTPConflict(text="A Voice device is already connected")
            socket = web.WebSocketResponse(heartbeat=20, max_msg_size=16384)
            await socket.prepare(request)
            device = VoiceDevice(socket, app[HTTP], settings, app[BACKEND])
            connections.add(device)
        try:
            async for message in socket:
                await device.receive(message)
        except (ValueError, aiohttp.ClientError, asyncio.TimeoutError):
            LOG.warning("Invalid or disconnected Voice device transport")
        finally:
            device.closing = True
            device.restart = device.accept_audio = False
            device.stop_for("device_connection_closed")
            await socket.close()
            await device.close()
            last_stop_reason = device.last_stop_reason
            connections.discard(device)
        return socket

    async def health(request):
        device = next((d for d in connections if not d.socket.closed), None)
        return web.json_response({"model": "gpt-live-1", "backend": settings.backend,
                                  "device_connected": bool(device and not device.socket.closed),
                                  "audio_active": bool(device and device.accept_audio),
                                  "last_stop_reason": device.last_stop_reason if device else last_stop_reason})

    app.cleanup_ctx.append(resources)
    app.on_shutdown.append(shutdown)
    app.router.add_get("/voice", voice)
    app.router.add_get("/health", health)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("demo", "home-assistant"), default="demo")
    parser.add_argument("--bind", default=os.getenv("VOICE_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--idle-timeout", type=int, default=90)
    parser.add_argument("--max-duration", type=int, default=600)
    args = parser.parse_args()
    settings = DeviceSettings(**vars(args), api_key=os.getenv("OPENAI_API_KEY", ""),
                              device_token=os.getenv("VOICE_DEVICE_TOKEN", ""), room=os.getenv("VOICE_ROOM", ""),
                              ha_url=os.getenv("HA_URL", ""), ha_token=os.getenv("HA_TOKEN", ""),
                              ha_agent_id=os.getenv("HA_AGENT_ID", "conversation.special_agent"))
    logging.basicConfig(level=logging.INFO)
    # Access logs include query strings, so disable them to keep the device token private.
    web.run_app(create_app(settings), host=settings.bind, port=settings.port, access_log=None)


if __name__ == "__main__":
    main()
