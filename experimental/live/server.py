"""Run with: python -m experimental.live.server [--backend home-assistant]."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp
from aiohttp import web

from .backend import DemoBackend, HomeAssistantBackend
from .session import LiveSession
from utils.constants import AGENT_MODELS, REASONING_EFFORTS

LOG = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")
OPENAI_URL = "https://api.openai.com/v1/live/sessions"


@dataclass
class Settings:
    backend: str = "demo"
    port: int = 8099
    idle_timeout: int = 30
    max_duration: int = 0
    api_key: str = field(default="", repr=False)
    ha_url: str = ""
    ha_token: str = field(default="", repr=False)
    ha_agent_id: str = "conversation.special_agent"
    agent_model: str | None = None
    reasoning_effort: str | None = None
    fast_mode: bool | None = None

    def validate(self):
        if self.backend not in ("demo", "home-assistant"):
            raise ValueError("Unknown backend")
        if self.agent_model is not None and self.agent_model not in AGENT_MODELS:
            raise ValueError("Invalid agent_model option")
        if self.reasoning_effort is not None and self.reasoning_effort not in REASONING_EFFORTS:
            raise ValueError("Invalid reasoning_effort option")
        if self.fast_mode is not None and type(self.fast_mode) is not bool:
            raise ValueError("Invalid fast_mode option")
        if not 1 <= self.port <= 65535 or not 10 <= self.idle_timeout <= 600 or not 0 <= self.max_duration <= 1800:
            raise ValueError("Invalid port or timeout limits")
        if self.backend == "home-assistant":
            url = urlsplit(self.ha_url)
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("Set HA_URL to your Home Assistant HTTP(S) address without credentials or query parameters")
            if not self.ha_token or not self.ha_agent_id:
                raise ValueError("Set HA_TOKEN and HA_AGENT_ID for the Home Assistant backend")


def voice_instructions(mode, room=""):
    capabilities = (
        "The backend only runs a five-second demonstration task. It cannot control devices or look up facts. "
        "Clearly describe demo results as simulated."
        if mode == "demo" else
        "The backend is Special Agent in Home Assistant. It can check home state, use configured home/media tools, "
        "and look up current information. " +
        (f"This voice device is in {room}. Use that room when the user says here."
         if room else "This client has no room identity; ask which room when unclear.")
    )
    return (
        "You are Special Agent, a concise, natural home voice assistant. "
        "Backchannel policy: Use occasional brief acknowledgments. "
        "After answering, wait quietly for the user unless a pending task has a new result to report. "
        "Interruption policy: Stop speaking when interrupted and listen to the correction. "
        "Delegation policy:\nBackend tools:\n" + capabilities +
        "\nDelegate to the backend when:\n"
        "- A complete, unambiguous request needs its capabilities, current facts, or substantive reasoning.\n"
        "- The user answers a backend clarification/confirmation or corrects a previous task.\n"
        "Do not delegate to the backend when:\n"
        "- Greeting, asking a brief clarification, telling a simple joke, or reusing a current result.\n"
        "- The user has not finished specifying a request.\n"
        "Do not guess tool results. Ask the backend's confirmation questions and wait for the user's answer. "
        "You can keep conversing while work runs. Stopping speech does not cancel a home action; "
        "corrections are processed after any request already executing. Never claim cancellation or success without a backend result."
    )


class Bridge:
    def __init__(self, settings, http):
        self.settings, self.http = settings, http
        self.sessions = {}
        self.sockets = {}
        self.readers = {}
        self.create_lock = asyncio.Lock()
        self.backend = DemoBackend() if settings.backend == "demo" else HomeAssistantBackend(
            http, settings.ha_url, settings.ha_token, settings.ha_agent_id,
            model=settings.agent_model, reasoning_effort=settings.reasoning_effort,
            fast_mode=settings.fast_mode)

    async def create(self, sdp):
        if not self.settings.api_key:
            raise web.HTTPServiceUnavailable(text="Set OPENAI_API_KEY in the server environment and restart.")
        async with self.create_lock:
            if any(not self.sockets[s.id].closed or any(j.status == "running" for j in s.jobs.values())
                   for s in self.sessions.values()):
                raise web.HTTPConflict(text="End the active session and allow its backend task to finish first.")
            # Retain only a few finished runs; audio is never stored.
            for key in list(self.sessions)[:-4]:
                self.sessions.pop(key, None)
                self.sockets.pop(key, None)
                self.readers.pop(key, None)
            headers = {"Authorization": f"Bearer {self.settings.api_key}"}
            body = {"session": {"model": "gpt-live-1", "instructions": voice_instructions(self.settings.backend),
                                "delegation": {"type": "client"}},
                    "transport": {"type": "webrtc", "sdp": sdp}}
            try:
                async with self.http.post(OPENAI_URL, headers=headers, json=body,
                                          timeout=aiohttp.ClientTimeout(total=30), allow_redirects=False) as response:
                    if response.status != 201:
                        LOG.warning("Live creation returned HTTP %s", response.status)
                        raise web.HTTPBadGateway(text=f"GPT-Live session creation failed (HTTP {response.status}). Check model access and your API key.")
                    result = await response.json()
                live_id, answer = result["session"]["id"], result["transport"]["sdp"]
                if not isinstance(live_id, str) or not isinstance(answer, str):
                    raise ValueError("Invalid Live create response")
            except web.HTTPException:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError) as error:
                raise web.HTTPBadGateway(text="GPT-Live session creation could not be confirmed. It was not retried.") from error
            try:
                socket = await asyncio.wait_for(self.http.ws_connect(
                    OPENAI_URL.replace("https:", "wss:") + "/" + quote(live_id, safe="") + "/attach",
                    headers=headers, heartbeat=20, max_msg_size=2**20), timeout=15)
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                # The browser never received SDP; do not leave a billable orphan on attach failure.
                await self.hangup(live_id)
                raise web.HTTPBadGateway(text="Could not attach the backend to GPT-Live. The test did not start.") from error
            session = LiveSession(live_id, socket.send_json, self.backend)
            # Attach does not replay session.started. HTTP creation already started this session.
            session.state = "active"
            self.sessions[session.id], self.sockets[session.id] = session, socket
            self.readers[session.id] = asyncio.create_task(self.read_events(session, socket))
            return {"id": session.id, "session": {"id": live_id},
                    "transport": {"type": "webrtc", "sdp": answer}}

    async def hangup(self, live_id):
        try:
            async with self.http.post(OPENAI_URL + "/" + quote(live_id, safe="") + "/hangup",
                                      headers={"Authorization": f"Bearer {self.settings.api_key}"},
                                      timeout=aiohttp.ClientTimeout(total=10), allow_redirects=False) as response:
                if response.status >= 300:
                    LOG.warning("Live fallback hangup returned HTTP %s; final usage unconfirmed", response.status)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            LOG.warning("Live fallback hangup failed; final usage unconfirmed")

    async def read_events(self, session, socket):
        try:
            async for message in socket:
                if message.type == aiohttp.WSMsgType.TEXT:
                    event = message.json()
                    if event.get("type") == "error":
                        LOG.warning("Live API event error code: %s", event.get("error", {}).get("code", "unknown"))
                    await session.receive(event)
                    if session.finalized:
                        break
                elif message.type == aiohttp.WSMsgType.ERROR:
                    break
        except (aiohttp.ClientError, ValueError):
            LOG.warning("Live sideband disconnected unexpectedly")
        finally:
            if not session.finalized and session.state != "closing":
                session.error, session.state = "Live connection lost; final usage is unconfirmed.", "error"
                await self.hangup(session.live_id)
            await socket.close()

    async def close(self, session):
        await session.close()
        if not session.finalized:
            await self.hangup(session.live_id)
        await self.sockets[session.id].close()

    async def monitor(self):
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            for session in list(self.sessions.values()):
                expired = self.settings.max_duration > 0 and now - session.created_at > self.settings.max_duration
                idle = session.is_idle(now, self.settings.idle_timeout)
                local_error = session.state == "error"
                if session.state not in ("closing", "closed") and not self.sockets[session.id].closed and (expired or idle or local_error):
                    await self.close(session)

    async def shutdown(self):
        await asyncio.gather(*(self.close(s) for s in self.sessions.values() if not s.finalized), return_exceptions=True)
        # Preserve results of requests already accepted by HA, without treating a disconnect as cancellation.
        await asyncio.gather(*(s.drain() for s in self.sessions.values()), return_exceptions=True)
        await asyncio.gather(*self.readers.values(), return_exceptions=True)


SETTINGS = web.AppKey("settings", Settings)
BRIDGE = web.AppKey("bridge", Bridge)


@web.middleware
async def local_requests(request, handler):
    settings = request.app[SETTINGS]
    hosts = {f"localhost:{settings.port}", f"127.0.0.1:{settings.port}"}
    if request.host not in hosts:
        raise web.HTTPForbidden(text="Use the localhost address printed by the server.")
    if request.method not in ("GET", "HEAD") and request.headers.get("Origin") not in {"http://" + host for host in hosts}:
        raise web.HTTPForbidden(text="Unexpected request origin.")
    response = await handler(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'"
    return response


async def resources(app):
    async with aiohttp.ClientSession() as http:
        bridge = Bridge(app[SETTINGS], http)
        app[BRIDGE] = bridge
        monitor = asyncio.create_task(bridge.monitor())
        logs = (asyncio.create_task(bridge.backend.poll_activity())
                if isinstance(bridge.backend, HomeAssistantBackend) else None)
        try:
            yield
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            await bridge.shutdown()
            if logs is not None:
                logs.cancel()
                await asyncio.gather(logs, return_exceptions=True)


def create_app(settings):
    settings.validate()
    app = web.Application(middlewares=[local_requests], client_max_size=65536)
    app[SETTINGS] = settings
    app.cleanup_ctx.append(resources)

    async def config(request):
        return web.json_response({"backend": settings.backend, "model": "gpt-live-1",
                                  "idle_timeout": settings.idle_timeout, "max_duration": settings.max_duration})

    async def create(request):
        try:
            body = await request.json()
        except ValueError as error:
            raise web.HTTPBadRequest(text="Expected an SDP offer as JSON.") from error
        if not isinstance(body, dict) or not isinstance(body.get("sdp"), str) or not body["sdp"].startswith("v=0"):
            raise web.HTTPBadRequest(text="Expected a valid SDP offer.")
        return web.json_response(await request.app[BRIDGE].create(body["sdp"]), status=201)

    def lookup(request):
        session = request.app[BRIDGE].sessions.get(request.match_info["id"])
        if not session:
            raise web.HTTPNotFound(text="Session not found.")
        return session

    async def status(request):
        return web.json_response(lookup(request).snapshot())

    async def close(request):
        session = lookup(request)
        await request.app[BRIDGE].close(session)
        return web.json_response(session.snapshot())

    async def static(request):
        filename = request.match_info.get("file", "index.html")
        if filename not in {"index.html", "app.js", "style.css"}:
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC / filename)

    app.router.add_get("/api/config", config)
    app.router.add_post("/api/session", create)
    app.router.add_get("/api/session/{id}", status)
    app.router.add_post("/api/session/{id}/close", close)
    app.router.add_get("/static/{file}", static)
    app.router.add_get("/", static)
    app.router.add_get("/{file}", static)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("demo", "home-assistant"), default="demo")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--idle-timeout", type=int, default=30)
    parser.add_argument("--max-duration", type=int, default=0, help="Optional session length cap in seconds; 0 disables it")
    args = parser.parse_args()
    settings = Settings(**vars(args), api_key=os.getenv("OPENAI_API_KEY", ""),
                        ha_url=os.getenv("HA_URL", ""), ha_token=os.getenv("HA_TOKEN", ""),
                        ha_agent_id=os.getenv("HA_AGENT_ID", "conversation.special_agent"))
    logging.basicConfig(level=logging.INFO)
    try:
        app = create_app(settings)
    except ValueError as error:
        parser.error(str(error))
    print(f"Open http://localhost:{settings.port} — backend: {settings.backend}")
    web.run_app(app, host="127.0.0.1", port=settings.port, access_log=None)


if __name__ == "__main__":
    main()
