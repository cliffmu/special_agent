"""Live trace correlation, delivery and opt-in payload relay without paid APIs."""

import asyncio
import json
from contextlib import asynccontextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from experimental.live.backend import BackendResult, HomeAssistantBackend
from experimental.live import session as session_module
from experimental.live.session import LiveSession
from utils import logging as log


@pytest.fixture(autouse=True)
def trace_settings():
    log.configure_trace(enabled=False)
    yield
    log.configure_trace(enabled=False)


def lines(caplog, event):
    return [raw_line(record) for record in caplog.records
            if raw_line(record).startswith(f"event={event} ")]


def raw_line(record):
    return getattr(record, "special_agent_activity", record.getMessage())


def transcript_text(line):
    return json.loads(line.split(" payload=", 1)[1])["text"]


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
        assert any(record.name == "experimental.live.backend.home_assistant" and raw_line(record) == detail_line
                   for record in caplog.records)
    else:
        with pytest.raises(ValueError, match="Invalid activity record"):
            await backend._poll_activity_page()
        assert queries == [{"cursor": "0", "limit": "200"}]
        assert backend.activity_cursor == 0
        assert not any(record.name == "experimental.live.backend.home_assistant" for record in caplog.records)


async def test_smalltalk_groups_overlapping_speaker_fragments_after_display_pause(caplog, monkeypatch):
    monkeypatch.setattr(session_module, "TRANSCRIPT_DISPLAY_PAUSE_SECONDS", 0.02)
    log.configure_trace(enabled=True)
    backend = SimpleNamespace(execute=AsyncMock())
    session = LiveSession("upstream", AsyncMock(), backend)
    fragments = [
        ("input", "Hi ", 0, 100),
        ("output", "Hello", 80, 200),
        ("input", "there!", 100, 300),
        ("output", ", Cliff.", 200, 450),
    ]
    for speaker, text, start, end in fragments:
        await session.receive({"type": f"session.{speaker}_transcript.delta", "delta": text,
                               "start_ms": start, "end_ms": end})
    assert not lines(caplog, "live_transcript")
    await asyncio.sleep(0.04)
    captured = lines(caplog, "live_transcript")
    assert [transcript_text(line) for line in captured] == ["Hi there!", "Hello, Cliff."]
    assert "mode=user" in captured[0] and "start_ms=0" in captured[0] and "end_ms=300" in captured[0]
    assert "mode=assistant" in captured[1] and "start_ms=80" in captured[1] and "end_ms=450" in captured[1]
    assert all("phase=captured" in line and "reason=display_pause" in line
               and f"session={session.id[:10]}" in line for line in captured)
    assert [item["text"] for item in session.transcripts] == [item[1] for item in fragments]
    backend.execute.assert_not_awaited()
    assert session._pending_text == "Hi there!" and session.state == "connecting"
    await session.receive({"type": "session.closed"})
    assert len(lines(caplog, "live_transcript")) == 2


async def test_paused_transcript_continuation_is_logged_once_without_replaying_prefix(caplog, monkeypatch):
    monkeypatch.setattr(session_module, "TRANSCRIPT_DISPLAY_PAUSE_SECONDS", 0.02)
    log.configure_trace(enabled=True)
    session = LiveSession("upstream", AsyncMock(), SimpleNamespace(execute=AsyncMock()))
    first = {"type": "session.output_transcript.delta", "delta": "The story begins",
             "event_id": "fragment-1"}
    await session.receive(first)
    await asyncio.sleep(0.04)
    await session.receive(first)  # Provider event replay must not repeat its text.
    await session.receive({"type": "session.output_transcript.delta", "delta": " with a fox."})
    await session.receive({"type": "session.closed"})
    await asyncio.sleep(0.04)
    captured = lines(caplog, "live_transcript")
    assert [transcript_text(line) for line in captured] == ["The story begins", " with a fox."]
    assert "reason=session_closed" in captured[1]
    assert not session._trace_transcripts


async def test_delegation_flush_and_actual_live_reply_have_distinct_transcript_blocks(caplog):
    log.configure_trace(enabled=True)
    backend = SimpleNamespace(execute=AsyncMock(return_value=BackendResult("Backend result")))
    session = LiveSession("upstream", AsyncMock(), backend, settle_seconds=0)
    await session.receive(transcript("Check "))
    await session.receive(transcript("the lamp."))
    await session.receive(delegate())
    await session.drain()
    captured = lines(caplog, "live_transcript")
    assert len(captured) == 1 and transcript_text(captured[0]) == "Check the lamp."
    assert "reason=delegation_dispatch" in captured[0]
    assert "job=-" in captured[0]
    # The backend result is not presented as a transcript of Live's spoken reply.
    await session.receive({"type": "session.output_transcript.delta", "delta": "Your lamp "})
    await session.receive({"type": "session.output_transcript.delta", "delta": "is on."})
    await session.receive({"type": "session.closed"})
    captured = lines(caplog, "live_transcript")
    assert [transcript_text(line) for line in captured] == ["Check the lamp.", "Your lamp is on."]
    assert "mode=assistant" in captured[1]
    backend.execute.assert_awaited_once()


@pytest.mark.parametrize("boundary", ["closed", "error", "close", "close_timeout"])
async def test_transcript_end_paths_flush_once_and_cancel_pending_display_timer(caplog, monkeypatch, boundary):
    monkeypatch.setattr(session_module, "TRANSCRIPT_DISPLAY_PAUSE_SECONDS", 0.02)
    log.configure_trace(enabled=True)

    async def send(event):
        if boundary != "close_timeout" and event["type"] == "session.close":
            await session.receive({"type": "session.closed"})

    session = LiveSession("upstream", send, SimpleNamespace(execute=AsyncMock()))
    await session.receive({"type": "session.output_transcript.delta", "delta": "A short reply."})
    if boundary == "closed":
        await session.receive({"type": "session.closed"})
    elif boundary == "error":
        await session.receive({"type": "error"})
    else:
        await session.close(timeout=0.001)
    assert not session._trace_transcripts
    await session.receive({"type": "session.output_transcript.delta", "delta": "Late provider fragment."})
    assert not session._trace_transcripts
    await asyncio.sleep(0.04)
    captured = lines(caplog, "live_transcript")
    assert len(captured) == 1 and transcript_text(captured[0]) == "A short reply."
    assert "phase=captured" in captured[0]
    assert "phase=completed" not in captured[0] and "phase=delivered" not in captured[0]


async def test_trace_off_does_not_buffer_or_emit_transcripts_and_drops_pending_details(caplog, monkeypatch):
    monkeypatch.setattr(session_module, "TRANSCRIPT_DISPLAY_PAUSE_SECONDS", 0.02)
    session = LiveSession("upstream", AsyncMock(), SimpleNamespace(execute=AsyncMock()))
    await session.receive(transcript("PRIVATE_BEFORE_TRACE"))
    assert not session._trace_transcripts
    log.configure_trace(enabled=True)
    await session.receive(transcript("PRIVATE_PENDING"))
    log.configure_trace(enabled=False)
    await session.receive(transcript("PRIVATE_AFTER_TRACE"))
    assert not session._trace_transcripts
    await asyncio.sleep(0.04)
    assert not lines(caplog, "live_transcript")
    log.configure_trace(enabled=True)
    await session.receive({"type": "session.output_transcript.delta", "delta": "Visible now."})
    await session.receive({"type": "session.closed"})
    assert [transcript_text(line) for line in lines(caplog, "live_transcript")] == ["Visible now."]
    assert "PRIVATE" not in caplog.text


async def test_transcript_buffer_is_bounded_and_later_continuation_survives(caplog):
    log.configure_trace(enabled=True)
    session = LiveSession("upstream", AsyncMock(), SimpleNamespace(execute=AsyncMock()))
    await session.receive({"type": "session.output_transcript.delta", "delta": "x" * 16000 + "The ending."})
    assert len(session._trace_transcripts["assistant"]["text"]) < 16000
    await session.receive({"type": "session.closed"})
    captured = lines(caplog, "live_transcript")
    assert len(captured) == 2 and "reason=buffer_limit" in captured[0]
    assert "[truncated]" in transcript_text(captured[0])
    assert transcript_text(captured[1]) == "The ending."


async def test_transcript_redaction_sees_complete_fragmented_secret(caplog):
    log.configure_trace(enabled=True)
    session = LiveSession("upstream", AsyncMock(), SimpleNamespace(execute=AsyncMock()))
    await session.receive(transcript("Remember password=\"private"))
    await session.receive(transcript("-password\" for later."))
    await session.receive({"type": "session.closed"})
    captured = lines(caplog, "live_transcript")
    assert len(captured) == 1 and "[redacted]" in transcript_text(captured[0])
    assert "private-password" not in caplog.text
    assert log.is_trace_line(captured[0])


@pytest.mark.parametrize("prefix,continuation", [
    ("Remember password=", "SAMPLE_SECRET_VALUE for later."),
    ('Remember password="private ', 'SAMPLE_SECRET_VALUE" for later.'),
    ("Remember pass", "word=SAMPLE_SECRET_VALUE for later."),
    ("Use sk-", "SAMPLE_SECRET_VALUE for later."),
    ("Use Bearer ", "SAMPLE_SECRET_VALUE for later."),
    ("Visit https://host/", "SAMPLE_SECRET_VALUE for later."),
    ("Token eyJheader", ".SAMPLE_SECRET_VALUE.signature for later."),
])
async def test_paused_transcript_secrets_keep_redaction_context(caplog, monkeypatch, prefix, continuation):
    monkeypatch.setattr(session_module, "TRANSCRIPT_DISPLAY_PAUSE_SECONDS", 0.01)
    log.configure_trace(enabled=True)
    backend = SimpleNamespace(execute=AsyncMock())
    session = LiveSession("upstream", AsyncMock(), backend)
    await session.receive(transcript(prefix))
    await asyncio.sleep(0.025)
    assert len(lines(caplog, "live_transcript")) == 1
    await session.receive(transcript(continuation))
    await session.receive({"type": "session.closed"})
    captured = lines(caplog, "live_transcript")
    assert len(captured) == 2
    assert "SAMPLE_SECRET_VALUE" not in caplog.text
    assert "[redacted]" in " ".join(transcript_text(line) for line in captured)
    assert " for later." in transcript_text(captured[-1])
    assert all(log.is_trace_line(line) for line in captured)
    assert [item["text"] for item in session.transcripts] == [prefix, continuation]
    assert session._pending_text == prefix + continuation
    assert not session._trace_redactors
    backend.execute.assert_not_awaited()


@pytest.mark.parametrize("prefix,continuation", [
    ("password=", "SAMPLE_SECRET_VALUE finished."),
    ('password="', 'SAMPLE_SECRET_VALUE" finished.'),
    ("sk-", "SAMPLE_SECRET_VALUE finished."),
    ("https://host/", "SAMPLE_SECRET_VALUE finished."),
])
async def test_secret_at_transcript_buffer_boundary_is_redacted(caplog, prefix, continuation):
    log.configure_trace(enabled=True)
    session = LiveSession("upstream", AsyncMock(), SimpleNamespace(execute=AsyncMock()))
    first = "x" * (16000 - len(prefix) - 1) + " " + prefix
    full_text = first + continuation
    await session.receive({"type": "session.output_transcript.delta", "delta": full_text})
    assert len(lines(caplog, "live_transcript")) == 1
    await session.receive({"type": "session.closed"})
    captured = lines(caplog, "live_transcript")
    assert len(captured) == 2 and "reason=buffer_limit" in captured[0]
    assert "SAMPLE_SECRET_VALUE" not in caplog.text
    assert "finished." in transcript_text(captured[-1])
    assert session.transcripts[-1]["text"] == full_text
    assert all(log.is_trace_line(line) for line in captured)


async def test_transcript_secret_context_is_independent_between_speakers(caplog):
    log.configure_trace(enabled=True)
    session = LiveSession("upstream", AsyncMock(), SimpleNamespace(execute=AsyncMock()))
    await session.receive(transcript("password="))
    session._flush_transcripts("display_pause")
    await session.receive({"type": "session.output_transcript.delta", "delta": "Other speaker's clear response."})
    await session.receive(transcript("SAMPLE_SECRET_VALUE"))
    await session.receive({"type": "session.closed"})
    captured = lines(caplog, "live_transcript")
    assert "SAMPLE_SECRET_VALUE" not in caplog.text
    assert any(transcript_text(line) == "Other speaker's clear response." for line in captured)
    assert transcript_text(captured[-1]) == "[redacted]"
