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

from experimental.live.backend import ACTIVITY_PATH, PROCESS_PATH, BackendError, HomeAssistantBackend
from experimental.live import device_server, server as browser_server
from utils.logging import read_activity


def reply(text="Done"):
    return {"conversation_id": "continued-conversation", "response": {
        "response_type": "action_done", "speech": {"plain": {"speech": text}}}}


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
        assert "0.3.2" in str(error.value)
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
