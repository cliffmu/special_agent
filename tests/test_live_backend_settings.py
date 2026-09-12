"""Settings and activity transport checks using local HTTP servers only."""

import asyncio
from contextlib import asynccontextmanager
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from experimental.live.backend import ACTIVITY_PATH, PROCESS_PATH, BackendError, HomeAssistantBackend, _safe_activity_line
from experimental.live import device_server, server as browser_server
from utils.logging import read_activity


def reply(text="Done"):
    return {"conversation_id": "continued-conversation", "response": {
        "response_type": "action_done", "speech": {"plain": {"speech": text}}}}


def test_activity_transport_accepts_only_hashed_device_labels():
    assert _safe_activity_line("event=request request=test device=012345abcd")
    assert not _safe_activity_line("event=request request=test device=live:primary")
    assert not _safe_activity_line("event=request request=test device=room_name")


@pytest.mark.parametrize("overrides", [
    {},
    {"model": "gpt-5.6-terra", "reasoning_effort": "low", "fast_mode": False},
    {"model": "gpt-6-astra", "reasoning_effort": "medium", "fast_mode": True},
])
async def test_backend_forwards_only_explicit_overrides_and_retains_fast_off(overrides):
    bodies = []

    async def process(request):
        assert request.headers["Authorization"] == "Bearer existing-ha-token"
        bodies.append(await request.json())
        return web.json_response(reply())

    app = web.Application()
    app.router.add_post(PROCESS_PATH, process)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        backend = HomeAssistantBackend(http, str(server.make_url("/")), "existing-ha-token",
                                       "conversation.test_agent", "Test room", **overrides)
        result = await backend.execute("Turn on the lights", [], "existing-conversation")
    assert len(bodies) == 1
    body = bodies[0]
    assert {key: body[key] for key in ("model", "reasoning_effort", "fast_mode") if key in body} == overrides
    assert body["agent_id"] == "conversation.test_agent"
    assert body["conversation_id"] == "existing-conversation"
    assert "device_id" not in body
    assert result.text == "Done" and result.conversation_id == "continued-conversation"


async def test_two_device_backends_run_independently_without_transport_leaks(caplog):
    caplog.set_level(logging.INFO)
    first_started, release_first = asyncio.Event(), asyncio.Event()
    received = []

    async def process(request):
        body = await request.json()
        assert request.path == PROCESS_PATH and not request.query
        assert request.headers["Authorization"] == "Bearer PRIVATE_HA_TOKEN"
        assert "PRIVATE_HA_TOKEN" not in str(body)
        received.append(body)
        if body["device_id"] == "device-a":
            first_started.set()
            await release_first.wait()
        return web.json_response(reply(body["device_id"] + " finished"))

    app = web.Application()
    app.router.add_post(PROCESS_PATH, process)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        backend_a = HomeAssistantBackend(http, str(server.make_url("/")), "PRIVATE_HA_TOKEN",
                                         "conversation.agent", "Room A", device_id="device-a")
        backend_b = HomeAssistantBackend(http, str(server.make_url("/")), "PRIVATE_HA_TOKEN",
                                         "conversation.agent", "Room B", device_id="device-b")
        task_a = asyncio.create_task(backend_a.execute("PRIVATE_REQUEST_A", [], "conversation-a"))
        try:
            await asyncio.wait_for(first_started.wait(), 1)
            result_b = await asyncio.wait_for(backend_b.execute("PRIVATE_REQUEST_B", [], "conversation-b"), 1)
            assert result_b.text == "device-b finished" and not task_a.done()
            assert [body["device_id"] for body in received] == ["device-a", "device-b"]
        finally:
            release_first.set()
            result_a = await asyncio.wait_for(task_a, 1)
    assert result_a.text == "device-a finished"
    for body, suffix in zip(received, ("a", "b")):
        assert body["conversation_id"] == "conversation-" + suffix
        assert "PRIVATE_REQUEST_" + suffix.upper() in body["text"]
        other = "B" if suffix == "a" else "A"
        assert "PRIVATE_REQUEST_" + other not in body["text"]
        assert f'"Room {suffix.upper()}"' in body["text"]
    assert "PRIVATE" not in caplog.text
    assert "device-a" not in caplog.text and "device-b" not in caplog.text


async def test_one_device_backend_serializes_successive_voice_sessions():
    first_started, second_started, release_first = asyncio.Event(), asyncio.Event(), asyncio.Event()
    received, order = [], []

    async def process(request):
        body = await request.json()
        assert not request.query and body["device_id"] == "device-a"
        received.append(body["conversation_id"])
        identity = body["conversation_id"]
        order.append(identity + " started")
        if identity == "first-voice-session":
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        order.append(identity + " finished")
        return web.json_response(reply(identity))

    app = web.Application()
    app.router.add_post(PROCESS_PATH, process)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        backend = HomeAssistantBackend(http, str(server.make_url("/")), "token", "agent", device_id="device-a")
        first = asyncio.create_task(backend.execute("first", [], "first-voice-session"))
        second = None
        try:
            await asyncio.wait_for(first_started.wait(), 1)
            second = asyncio.create_task(backend.execute("second", [], "second-voice-session"))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(second_started.wait(), 0.05)
            assert not first.done() and not second.done()
            assert received == ["first-voice-session"]
        finally:
            release_first.set()
            tasks = [task for task in (first, second) if task is not None]
            results = await asyncio.wait_for(asyncio.gather(*tasks), 1)
    assert [result.text for result in results] == ["first-voice-session", "second-voice-session"]
    assert order == ["first-voice-session started", "first-voice-session finished",
                     "second-voice-session started", "second-voice-session finished"]


@pytest.mark.parametrize("status", [404, 307])
async def test_missing_or_redirected_endpoint_never_falls_back_or_replays(status, caplog):
    requested = []

    async def missing(request):
        requested.append(request.path)
        return web.Response(status=status, text="PRIVATE_ERROR_BODY", headers={"Location": "/api/conversation/process"})

    async def fallback(request):
        requested.append(request.path)
        return web.json_response(reply("must never be called"))

    app = web.Application()
    app.router.add_post(PROCESS_PATH, missing)
    app.router.add_route("*", "/api/conversation/process", fallback)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        backend = HomeAssistantBackend(http, str(server.make_url("/")), "PRIVATE_TOKEN", "agent")
        with pytest.raises(BackendError) as error:
            await backend.execute("PRIVATE_REQUEST", [], None)
    assert requested == [PROCESS_PATH]
    assert "not retried" in str(error.value)
    if status == 404:
        assert "0.3.3" in str(error.value)
    assert "PRIVATE" not in caplog.text and "PRIVATE" not in str(error.value)


def record(cursor, name):
    return {"cursor": cursor, "line": f"event=tool request=abc phase=finished tool={name}"}


def page(epoch, cursor, records=(), *, reset=False, has_more=False):
    return {"epoch": epoch, "cursor": cursor, "reset": reset,
            "has_more": has_more, "records": list(records)}


def forwarded(caplog):
    return [entry.getMessage() for entry in caplog.records
            if entry.name == "experimental.live.backend.home_assistant"]


async def test_activity_pagination_deduplicates_and_handles_overflow_and_restart(caplog):
    caplog.set_level(logging.INFO)
    pages = [
        page("epoch-a", 2, [record(1, "first"), record(2, "second")], has_more=True),
        page("epoch-a", 3, [record(2, "second"), record(3, "third")]),
        page("epoch-a", 7, [record(6, "sixth"), record(7, "seventh")], reset=True),
        page("epoch-b", 1, [record(1, "new-first")], reset=True),
        page("epoch-b", 2, [record(1, "new-first"), record(2, "new-second")]),
    ]
    queries, complete = [], asyncio.Event()

    async def activity(request):
        assert request.headers["Authorization"] == "Bearer preserved-token"
        queries.append(dict(request.query))
        if len(queries) > len(pages):
            complete.set()
            return web.json_response(page("epoch-b", 2))
        return web.json_response(pages[len(queries) - 1])

    app = web.Application()
    app.router.add_get(ACTIVITY_PATH, activity)
    before = read_activity()
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        backend = HomeAssistantBackend(http, str(server.make_url("/")), "preserved-token", "agent")
        backend.activity_poll_interval = 0.001
        task = asyncio.create_task(backend.poll_activity())
        try:
            await asyncio.wait_for(complete.wait(), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert queries[:5] == [
        {"cursor": "0", "limit": "200"},
        {"cursor": "2", "limit": "200", "epoch": "epoch-a"},
        {"cursor": "3", "limit": "200", "epoch": "epoch-a"},
        {"cursor": "7", "limit": "200", "epoch": "epoch-a"},
        {"cursor": "1", "limit": "200", "epoch": "epoch-b"},
    ]
    assert [line.rsplit("=", 1)[1] for line in forwarded(caplog)] == [
        "first", "second", "third", "sixth", "seventh", "new-first", "new-second",
    ]
    assert (backend.activity_epoch, backend.activity_cursor) == ("epoch-b", 2)
    assert read_activity()["cursor"] == before["cursor"]  # Forwarding must not recapture itself.


async def test_activity_recovers_from_errors_without_body_leaks_or_repeated_warnings(caplog):
    caplog.set_level(logging.INFO)
    queries, complete = [], asyncio.Event()

    async def activity(request):
        queries.append(dict(request.query))
        if len(queries) == 1:
            return web.Response(status=503, text="PRIVATE_SERVER_ERROR")
        if len(queries) == 2:
            return web.json_response(page("epoch-a", 1, [{"cursor": 1, "line":
                "event=tool request=abc secret=PRIVATE_CREDENTIAL"}]))
        if len(queries) == 3:
            return web.json_response(page("epoch-a", 1, [record(1, "recovered")]))
        complete.set()
        return web.json_response(page("epoch-a", 1))

    app = web.Application()
    app.router.add_get(ACTIVITY_PATH, activity)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        backend = HomeAssistantBackend(http, str(server.make_url("/")), "PRIVATE_TOKEN", "agent")
        backend.activity_poll_interval = 0.001
        task = asyncio.create_task(backend.poll_activity())
        try:
            await asyncio.wait_for(complete.wait(), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert queries[:3] == [{"cursor": "0", "limit": "200"}] * 3
    assert queries[3]["cursor"] == "1" and queries[3]["epoch"] == "epoch-a"
    assert forwarded(caplog) == [record(1, "recovered")["line"]]
    assert caplog.text.count("activity unavailable") == 1
    assert caplog.text.count("connection restored") == 1
    assert "PRIVATE" not in caplog.text


async def test_activity_cancellation_exits_inflight_request_without_retry():
    started, exited = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def get(*args, **kwargs):
        assert kwargs["allow_redirects"] is False
        started.set()
        try:
            await asyncio.Event().wait()
            yield
        finally:
            exited.set()

    http = SimpleNamespace(get=Mock(side_effect=get))
    backend = HomeAssistantBackend(http, "http://unused.invalid", "local-token", "agent")
    task = asyncio.create_task(backend.poll_activity())
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert exited.is_set()
    http.get.assert_called_once()


@pytest.mark.parametrize("client_type", ["hardware", "browser"])
async def test_app_owns_one_activity_poller_and_cancels_it_on_cleanup(monkeypatch, client_type):
    started, stopped = asyncio.Event(), asyncio.Event()
    instances = []

    async def poll(backend):
        instances.append(backend)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(HomeAssistantBackend, "poll_activity", poll)
    values = {"backend": "home-assistant", "ha_url": "http://unused.invalid", "ha_token": "local-token",
              "api_key": "fake-cloud-key", "agent_model": "gpt-5.6-terra", "reasoning_effort": "low",
              "fast_mode": False}
    token = "preserved-device-token-0123456789abcdef"
    if client_type == "hardware":
        settings = device_server.DeviceSettings(**values, device_token=token)
        app = device_server.create_app(settings)
    else:
        app = browser_server.create_app(browser_server.Settings(**values))
    async with TestClient(TestServer(app)) as client:
        await asyncio.wait_for(started.wait(), 1)
        if client_type == "hardware":
            # Connecting a replacement satellite must not create another HA poller.
            for _ in range(2):
                socket = await client.ws_connect("/voice", params={"token": token})
                await socket.close()
        assert len(instances) == 1
        assert (instances[0].model, instances[0].reasoning_effort, instances[0].fast_mode) == (
            "gpt-5.6-terra", "low", False,
        )
        assert not stopped.is_set()
    assert stopped.is_set()
    assert len(instances) == 1
