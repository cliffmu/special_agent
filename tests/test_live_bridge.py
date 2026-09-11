"""Protocol and lifecycle tests use fake Live events; no paid API or HA actions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from experimental.live.backend import BackendError, BackendResult, HomeAssistantBackend
from experimental.live.server import BRIDGE, Settings, create_app
from experimental.live.session import LiveSession, commentary_chunks
from experimental.live import session as session_module


def transcript(text, event_id="transcript-1"):
    return {"type": "session.input_transcript.delta", "delta": text,
            "start_ms": 0, "end_ms": 100, "event_id": event_id}


def delegation(job_id="job-1"):
    return {"type": "session.delegation.created", "offset_ms": 100,
            "delegation": {"id": job_id, "target": "client"}}


async def test_idle_grace_starts_at_readiness_and_resets_on_recognized_speech(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(session_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    session = LiveSession("live-test", AsyncMock(), SimpleNamespace())
    now[0] = 100.0
    await session.receive({"type": "session.started"})
    assert not session.is_idle(129.999, 30)
    assert session.is_idle(130.0, 30)
    now[0] = 125.0
    await session.receive(transcript("Another question"))
    assert not session.is_idle(154.999, 30)
    assert session.is_idle(155.0, 30)


async def test_idle_grace_follows_audible_playback_but_not_generated_silence(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(session_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    session = LiveSession("live-test", AsyncMock(), SimpleNamespace())
    session.mark_playback(45.0)  # Long speech must outlive an ordinary 30 s idle window.
    assert not session.is_idle(140.0, 30)
    assert not session.is_idle(174.999, 30)
    assert session.is_idle(175.0, 30)
    session.mark_playback(100.0, audible=False)
    assert session.playback_until == 245.0
    assert session.is_idle(175.0, 30), "continuous silent output cannot keep a paid session open"
    now[0] = 110.0
    await session.receive(transcript("Make it shorter"))
    assert session.last_input_at == 110.0, "transcript debounce must not inherit a future playback timestamp"
    assert session.last_activity == 145.0


@pytest.mark.parametrize("outcome", ["completed", "failed", "needs_context"])
async def test_every_backend_completion_starts_a_fresh_full_idle_window(monkeypatch, outcome):
    now = [100.0]
    monkeypatch.setattr(session_module, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def execute(*args):
        now[0] = 300.0
        if outcome == "failed":
            raise BackendError("A known failure")
        return BackendResult("Done")

    async def send(event):
        now[0] = 300.0

    session = LiveSession("live-test", send, SimpleNamespace(execute=execute),
                          settle_seconds=0, context_timeout=0)
    if outcome != "needs_context":
        await session.receive(transcript("Do the work"))
    await session.receive(delegation())
    assert not session.is_idle(250.0, 30), "queued work suppresses idle before it starts"
    await session.drain()
    assert session.jobs["job-1"].status == outcome
    assert not session.is_idle(329.999, 30)
    assert session.is_idle(330.0, 30)


@pytest.mark.parametrize("outcome", ["completed", "failed", "needs_context"])
async def test_idle_waits_for_blocked_result_delivery_then_gives_full_grace(monkeypatch, outcome):
    now = [100.0]
    monkeypatch.setattr(session_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    sending, release = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        now[0] = 300.0
        if outcome == "failed":
            raise BackendError("A known failure")
        return BackendResult("Done")

    async def send(event):
        sending.set()
        await release.wait()

    session = LiveSession("live-test", send, SimpleNamespace(execute=execute),
                          settle_seconds=0, context_timeout=0)
    if outcome != "needs_context":
        await session.receive(transcript("Do the work"))
    await session.receive(delegation())
    try:
        await asyncio.wait_for(sending.wait(), 1)
        assert session.jobs["job-1"].status == outcome
        now[0] = 400.0
        assert not session.is_idle(now[0], 30), "terminal job status is not completed result delivery"
        release.set()
        await session.drain()
        assert not session.is_idle(429.999, 30)
        assert session.is_idle(430.0, 30)
    finally:
        release.set()
        await session.drain()


async def test_audio_events_continue_while_backend_waits():
    running, release = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        running.set()
        await release.wait()
        return BackendResult("Lights on", "ha-conversation")

    sent = []
    session = LiveSession("live-test", AsyncMock(side_effect=sent.append),
                          SimpleNamespace(execute=execute), settle_seconds=0)
    await session.receive(transcript("Turn on the light"))
    await session.receive(delegation())
    await running.wait()
    await session.receive({"type": "session.output_transcript.delta", "delta": "I am working on that."})
    await session.receive({"type": "session.usage.updated", "usage": {"seconds": 8}})
    await session.receive({"type": "session.usage.updated", "usage": {"seconds": 9}})
    assert session.snapshot()["seconds"] == 9
    assert session.jobs["job-1"].status == "running"
    assert session.transcripts[-1]["role"] == "assistant"
    release.set()
    await session.drain()
    assert sent[-1]["type"] == "session.commentary.append"
    assert sent[-1]["delegation_id"] == "job-1"
    assert session.conversation_id == "ha-conversation"


async def test_duplicate_delegation_and_transcript_execute_only_once():
    backend = SimpleNamespace(execute=AsyncMock(return_value=BackendResult("done")))
    session = LiveSession("live-test", AsyncMock(), backend, settle_seconds=0)
    await session.receive(transcript("Turn "))
    await session.receive(transcript("Turn "))
    await session.receive(transcript("it on", "transcript-2"))
    await session.receive(delegation())
    await session.receive(delegation())
    await session.drain()
    assert backend.execute.await_count == 1
    assert backend.execute.call_args.args[0] == "Turn it on"


async def test_delegation_waits_for_late_transcript_and_never_executes_empty_request():
    backend = SimpleNamespace(execute=AsyncMock(return_value=BackendResult("done")))
    session = LiveSession("live-test", AsyncMock(), backend, settle_seconds=0, context_timeout=0.2)
    await session.receive(delegation())
    await asyncio.sleep(0)
    assert not backend.execute.called
    await session.receive(transcript("Please check the lights"))
    await session.drain()
    assert backend.execute.await_count == 1
    await session.receive(delegation("empty"))
    await session.drain()
    assert session.jobs["empty"].status == "needs_context"
    assert backend.execute.await_count == 1


async def test_transcript_without_delegation_never_runs_backend():
    backend = SimpleNamespace(execute=AsyncMock())
    session = LiveSession("live-test", AsyncMock(), backend, settle_seconds=0)
    await session.receive(transcript("Hello"))
    await asyncio.sleep(0)
    backend.execute.assert_not_called()


async def test_new_delegation_serializes_work_and_keeps_older_result_quiet():
    running, release = asyncio.Event(), asyncio.Event()
    requests, sent = [], []

    async def execute(request, history, conversation_id):
        requests.append(request)
        if len(requests) == 1:
            running.set()
            await release.wait()
        return BackendResult("Result " + str(len(requests)), "ha-id")

    session = LiveSession("live-test", AsyncMock(side_effect=sent.append),
                          SimpleNamespace(execute=execute), settle_seconds=0)
    await session.receive(transcript("Turn on the lights"))
    await session.receive(delegation())
    await running.wait()
    await session.receive(transcript("Actually leave the lights off", "t2"))
    await session.receive(delegation("job-2"))
    await asyncio.sleep(0)
    assert len(requests) == 1
    release.set()
    await session.drain()
    assert requests == ["Turn on the lights", "Actually leave the lights off"]
    assert sent[0]["type"] == "session.thinking.append"
    assert sent[-1]["type"] == "session.commentary.append"
    assert sent[-1]["delegation_id"] == "job-2"


async def test_close_collects_final_usage_without_canceling_accepted_ha_action():
    release, running = asyncio.Event(), asyncio.Event()
    sent = []

    async def send(event):
        sent.append(event)
        if event["type"] == "session.close":
            await session.receive({"type": "session.closed", "usage": {"seconds": 13}, "reason": "close_requested"})

    async def execute(*args):
        running.set()
        await release.wait()
        return BackendResult("It finished")

    session = LiveSession("live-test", send, SimpleNamespace(execute=execute), settle_seconds=0)
    await session.receive(transcript("Do a task"))
    await session.receive(delegation())
    await running.wait()
    await session.receive(delegation("never-started"))
    await session.close()
    assert session.finalized and session.usage["seconds"] == 13
    assert session.jobs["never-started"].status == "not_started"
    assert session.jobs["job-1"].status == "running"
    release.set()
    await session.drain()
    assert session.jobs["job-1"].status == "completed"
    assert sent == [{"type": "session.close"}]


async def test_close_without_final_event_reports_uncertainty():
    session = LiveSession("live-test", AsyncMock(), SimpleNamespace())
    await session.close(timeout=0.01)
    assert not session.finalized
    assert session.state == "error"
    assert "unknown" in session.error


async def test_api_error_blocks_queued_work_but_does_not_claim_running_action_canceled():
    running, release = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        running.set()
        await release.wait()
        return BackendResult("Finished")

    session = LiveSession("live-test", AsyncMock(), SimpleNamespace(execute=execute), settle_seconds=0)
    await session.receive(transcript("First action"))
    await session.receive(delegation())
    await running.wait()
    await session.receive(delegation("queued"))
    await session.receive({"type": "error", "error": {"code": "invalid_event"}})
    assert session.state == "error"
    assert session.jobs["queued"].status == "not_started"
    assert session.jobs["job-1"].status == "running"
    release.set()
    await session.drain()
    assert session.jobs["job-1"].status == "completed"


async def test_lost_result_delivery_marks_session_for_cleanup():
    session = LiveSession("live-test", AsyncMock(side_effect=ConnectionError), SimpleNamespace())
    await session.append("session.commentary.append", "job", "Done")
    assert session.state == "error" and not session.finalized


async def test_uncertain_ha_outcome_stops_queued_corrections():
    running, release = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        running.set()
        await release.wait()
        raise BackendError("The request timed out but may still finish.", uncertain=True)

    backend = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    session = LiveSession("live-test", AsyncMock(), backend, settle_seconds=0)
    await session.receive(transcript("Turn on the light"))
    await session.receive(delegation())
    await running.wait()
    await session.receive(transcript("Actually turn it off", "second"))
    await session.receive(delegation("correction"))
    release.set()
    await session.drain()
    assert backend.execute.await_count == 1
    assert session.jobs["correction"].status == "not_started"
    assert session.state == "error"


def test_commentary_byte_bound_preserves_unicode_and_result():
    result = "灯を消しました。 🏠 " * 300
    chunks = list(commentary_chunks(result))
    assert "".join(chunks) == result
    assert all(len(chunk.encode("utf-8")) <= 450 for chunk in chunks)


async def test_ha_adapter_preserves_conversation_and_reports_semantic_errors():
    bodies = []

    async def handle(request):
        assert request.headers["Authorization"] == "Bearer local-test-token"
        bodies.append(await request.json())
        return web.json_response({"conversation_id": "ha-123", "response": {
            "response_type": "error" if len(bodies) == 3 else "action_done",
            "speech": {"plain": {"speech": "No matching device" if len(bodies) == 3 else "Done"}}}})

    app = web.Application()
    app.router.add_post("/api/conversation/process", handle)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        backend = HomeAssistantBackend(http, str(server.make_url("/")), "local-test-token", "conversation.special_agent")
        result = await backend.execute("Turn it on", [], None)
        await backend.execute("Turn it off", [], result.conversation_id)
        with pytest.raises(BackendError, match="No matching device"):
            await backend.execute("Unknown device", [], result.conversation_id)
    assert "conversation_id" not in bodies[0]
    assert bodies[1]["conversation_id"] == "ha-123"
    assert all("device_id" not in body for body in bodies)


async def test_local_http_rejects_cross_origin_and_missing_key_without_exposing_config():
    settings = Settings(port=8099, ha_token="not-for-the-browser")
    async with TestClient(TestServer(create_app(settings))) as client:
        host = {"Host": "localhost:8099"}
        good = {**host, "Origin": "http://localhost:8099"}
        config = await client.get("/api/config", headers=host)
        assert "not-for-the-browser" not in await config.text()
        wrong = await client.post("/api/session", json={"sdp": "v=0"}, headers={**host, "Origin": "https://attacker.invalid"})
        assert wrong.status == 403
        missing = await client.post("/api/session", json={"sdp": "v=0"}, headers=good)
        assert missing.status == 503
        malformed = await client.post("/api/session", json=[], headers=good)
        assert malformed.status == 400
        rebinding = await client.get("/api/config", headers={"Host": "attacker.invalid:8099"})
        assert rebinding.status == 403
        assert not client.app[BRIDGE].sessions


@pytest.mark.parametrize("attach_fails", [False, True])
async def test_create_attach_and_shutdown_use_live_protocol(monkeypatch, attach_fails):
    from experimental.live import server as server_module
    received = []

    async def create(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        body = await request.json()
        assert body["session"]["model"] == "gpt-live-1"
        assert body["session"]["delegation"] == {"type": "client"}
        assert body["transport"] == {"type": "webrtc", "sdp": "v=0 offer"}
        assert "format" not in body["session"].get("audio", {})
        return web.json_response({"session": {"id": "live_opaque"},
                                  "transport": {"sdp": "v=0 answer"}}, status=201)

    async def attach(request):
        if attach_fails:
            raise web.HTTPServiceUnavailable()
        assert request.headers["Authorization"] == "Bearer test-key"
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        # Deliberately NO session.started: an attached socket does not replay it.
        async for message in socket:
            event = message.json()
            received.append(event)
            if event["type"] == "session.close":
                await socket.send_json({"type": "session.closed", "usage": {"seconds": 17}})
                break
        return socket

    async def hangup(request):
        received.append("hangup")
        return web.Response(status=200)

    upstream = web.Application()
    upstream.router.add_post("/v1/live/sessions", create)
    upstream.router.add_get("/v1/live/sessions/live_opaque/attach", attach)
    upstream.router.add_post("/v1/live/sessions/live_opaque/hangup", hangup)
    async with TestServer(upstream) as api:
        monkeypatch.setattr(server_module, "OPENAI_URL", str(api.make_url("/v1/live/sessions")))
        async with TestClient(TestServer(create_app(Settings(api_key="test-key")))) as client:
            headers = {"Host": "localhost:8099", "Origin": "http://localhost:8099"}
            response = await client.post("/api/session", json={"sdp": "v=0 offer"}, headers=headers)
            if attach_fails:
                assert response.status == 502
                assert received == ["hangup"]
                return
            assert response.status == 201
            result = await response.json()
            assert result["session"]["id"] == "live_opaque"
            session = client.app[BRIDGE].sessions[result["id"]]
            assert session.state == "active"
            conflict = await client.post("/api/session", json={"sdp": "v=0 offer"}, headers=headers)
            assert conflict.status == 409
            closed = await client.post(f"/api/session/{result['id']}/close", json={}, headers=headers)
            snapshot = await closed.json()
            assert snapshot["finalized"] and snapshot["seconds"] == 17
            assert received == [{"type": "session.close"}]
