"""Live trace correlation, delivery and opt-in payload relay without paid APIs."""

import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from experimental.live.backend import BackendResult, HomeAssistantBackend
from experimental.live.session import LiveSession
from utils import logging as log


@pytest.fixture(autouse=True)
def trace_settings():
    log.configure_trace(enabled=False)
    yield
    log.configure_trace(enabled=False)


def lines(caplog, event):
    return [record.getMessage() for record in caplog.records
            if record.getMessage().startswith(f"event={event} ")]


def delegate(job_id="PRIVATE_JOB"):
    return {"type": "session.delegation.created", "delegation": {"id": job_id, "target": "client"}}


def transcript(text):
    return {"type": "session.input_transcript.delta", "delta": text}


async def test_delegation_wait_and_http_request_share_safe_ids_without_payloads(caplog):
    entered, release = asyncio.Event(), asyncio.Event()
    bodies = []

    @asynccontextmanager
    async def post(*args, **kwargs):
        bodies.append(kwargs["json"])
        entered.set()
        await release.wait()
        yield SimpleNamespace(status=200, json=AsyncMock(return_value={"response": {
            "speech": {"plain": {"speech": "PRIVATE_RESULT"}}}}))

    backend = HomeAssistantBackend(SimpleNamespace(post=post), "http://private.invalid",
                                   "PRIVATE_TOKEN", "agent")
    session = LiveSession("PRIVATE_LIVE_ID", AsyncMock(), backend, settle_seconds=0)
    await session.receive(delegate())
    await asyncio.sleep(0)
    assert any("phase=context_wait" in line for line in lines(caplog, "delegation"))
    try:
        await session.receive(transcript("PRIVATE_REQUEST"))
        await asyncio.wait_for(entered.wait(), 1)
        correlation = {"session": session.id[:10], "job": sha256(b"PRIVATE_JOB").hexdigest()[:10]}
        assert bodies[0]["trace_context"] == correlation
        assert session.jobs["PRIVATE_JOB"].status == "running"
        assert all(all(f"{name}={value}" in line for name, value in correlation.items())
                   for event in ("delegation", "ha_request") for line in lines(caplog, event))
        assert any("route=agent_loop" in line and "phase=sent" in line for line in lines(caplog, "ha_request"))
    finally:
        release.set()
        await session.drain()
    assert any("phase=result_received" in line for line in lines(caplog, "delegation"))
    assert any("phase=sent" in line and "mode=commentary" in line for line in lines(caplog, "live_delivery"))
    assert "PRIVATE" not in caplog.text and "private.invalid" not in caplog.text
    assert log.job_correlation() is None


async def test_opt_in_trace_shows_request_and_returned_result_with_secret_redaction(caplog):
    log.configure_trace(enabled=True)
    session = LiveSession("upstream", AsyncMock(), SimpleNamespace(execute=AsyncMock(return_value=BackendResult(
        "The lamp is on; access_token=private-result-token"))), settle_seconds=0)
    await session.receive(transcript("Turn on the lamp; password=private-request-password"))
    await session.receive(delegate())
    await session.drain()
    request = lines(caplog, "delegation_request")
    result = lines(caplog, "delegation_result")
    assert len(request) == len(result) == 1
    assert "Turn on the lamp" in request[0] and "The lamp is on" in result[0]
    assert "private-request-password" not in caplog.text and "private-result-token" not in caplog.text
    assert all(log.is_trace_line(line) for line in request + result)
    assert not any("payload=" in row["line"] for row in log.read_activity()["records"])


async def test_delivery_failure_is_distinct_from_completed_backend_work(caplog):
    session = LiveSession("upstream", AsyncMock(side_effect=ConnectionError("PRIVATE_TRANSPORT_ERROR")),
                          SimpleNamespace(execute=AsyncMock(return_value=BackendResult("PRIVATE_RESULT"))),
                          settle_seconds=0)
    await session.receive(transcript("PRIVATE_REQUEST"))
    await session.receive(delegate())
    await session.drain()
    assert session.jobs["PRIVATE_JOB"].status == "completed" and session.state == "error"
    delivery = lines(caplog, "live_delivery")
    assert any("phase=failed" in line and "error_type=ConnectionError" in line for line in delivery)
    assert not any("phase=sent " in line for line in delivery)
    assert "PRIVATE" not in caplog.text


async def test_closing_audio_keeps_running_work_and_logs_undelivered_result(caplog):
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        entered.set()
        await release.wait()
        return BackendResult("Done")

    async def send(event):
        if event["type"] == "session.close":
            await session.receive({"type": "session.closed"})

    session = LiveSession("upstream", send, SimpleNamespace(execute=execute), settle_seconds=0)
    await session.receive(transcript("Do work"))
    await session.receive(delegate())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await session.close()
        assert session.jobs["PRIVATE_JOB"].status == "running"
        assert any("reason=running_actions_continue" in line and "remaining=1" in line
                   for line in lines(caplog, "live_session"))
    finally:
        release.set()
        await session.drain()
    assert session.jobs["PRIVATE_JOB"].status == "completed"
    assert any("phase=skipped" in line and "reason=closed" in line for line in lines(caplog, "live_delivery"))


async def test_cancelled_backend_queue_logs_not_sent_without_starting_http(caplog):
    http = SimpleNamespace(post=Mock())
    backend = HomeAssistantBackend(http, "http://unused.invalid", "token", "agent")
    await backend.lock.acquire()
    task = asyncio.create_task(backend.execute("Do work", [], None))
    try:
        await asyncio.sleep(0)
        assert any("phase=queue_wait" in line for line in lines(caplog, "ha_request"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert any("phase=queue_cancelled" in line and "status=not_sent" in line
                   for line in lines(caplog, "ha_request"))
        http.post.assert_not_called()
    finally:
        backend.lock.release()


async def test_cancelled_worker_finishes_trace_with_cancelled_status(caplog):
    entered = asyncio.Event()

    async def execute(*args):
        entered.set()
        await asyncio.Event().wait()

    session = LiveSession("upstream", AsyncMock(), SimpleNamespace(execute=execute), settle_seconds=0)
    await session.receive(transcript("Do work"))
    await session.receive(delegate())
    await asyncio.wait_for(entered.wait(), 1)
    session._worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session.drain()
    finished = [line for line in lines(caplog, "delegation") if "phase=finished" in line]
    assert len(finished) == 1 and "status=cancelled" in finished[0]
    assert "status=running" not in finished[0]
    assert any("reason=action_outcome_unconfirmed" in line for line in lines(caplog, "delegation"))


@pytest.mark.parametrize("enabled", [False, True])
async def test_payload_relay_requires_opt_in_and_retains_trace_correlations(caplog, enabled):
    log.configure_trace(enabled=True)
    log.trace_detail("tool_result", payload={"result": "Lamp is on"},
                     session="012345abcd", job="abcdef0123", call_id="123456789a", route="agent_loop")
    detail_line = lines(caplog, "tool_result")[-1]
    log.configure_trace(enabled=False)
    queries = []

    @asynccontextmanager
    async def get(*args, **kwargs):
        queries.append(kwargs["params"])
        yield SimpleNamespace(status=200, json=AsyncMock(return_value={
            "epoch": "epoch-a", "cursor": 1, "reset": False, "has_more": False,
            "records": [{"cursor": 1, "line": detail_line}],
        }))

    backend = HomeAssistantBackend(SimpleNamespace(get=get), "http://unused.invalid", "token", "agent",
                                   trace_logging=enabled)
    if enabled:
        assert await backend._poll_activity_page() is False
        assert queries == [{"cursor": "0", "limit": "200", "detail": "true"}]
        assert backend.activity_cursor == 1
        assert any(record.name == "experimental.live.backend.home_assistant" and record.getMessage() == detail_line
                   for record in caplog.records)
    else:
        with pytest.raises(ValueError, match="Invalid activity record"):
            await backend._poll_activity_page()
        assert queries == [{"cursor": "0", "limit": "200"}]
        assert backend.activity_cursor == 0
        assert not any(record.name == "experimental.live.backend.home_assistant" for record in caplog.records)
