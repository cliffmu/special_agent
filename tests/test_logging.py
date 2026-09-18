import logging
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from utils import logging as log


def test_info_log_level(hass, caplog):
    caplog.set_level(logging.INFO)
    log.info("Hello")
    assert "Hello" in caplog.text


def test_debug_toggle(hass, caplog):
    caplog.set_level(logging.DEBUG)
    log.debug("Verbose")
    assert "Verbose" in caplog.text


def test_activity_is_visible_without_enabling_legacy_info(caplog):
    previous = log._LOGGER.level
    try:
        log._LOGGER.setLevel(logging.WARNING)
        caplog.set_level(logging.INFO, logger=log._ACTIVITY_LOGGER.name)
        log.info("LEGACY_ARGUMENT_DUMP")
        log.activity("request", phase="received")
        assert "event=request" in caplog.text
        assert "LEGACY_ARGUMENT_DUMP" not in caplog.text
    finally:
        log._LOGGER.setLevel(previous)


def test_activity_allowlist_drops_payloads_and_redacts_unsafe_values(caplog):
    caplog.set_level(logging.INFO)

    class PrivateObject:
        def __str__(self):
            raise AssertionError("Private objects must never be stringified")

    log.activity("tool", tool="registered_tool", phase="finished", status="error", elapsed_ms=12,
                 prompt="PRIVATE_PROMPT", arguments={"secret": "PRIVATE_ARGUMENT"},
                 result="PRIVATE_RESULT", api_key="PRIVATE_API_KEY", model="sk-private-model-value",
                 error_type="ValueError\nPRIVATE_EXCEPTION", function_names=["registered_tool", PrivateObject()],
                 total_tokens=10**1000)
    line = caplog.records[-1].getMessage()
    assert "tool=registered_tool" in line and "elapsed_ms=12" in line
    assert "function_names=registered_tool,redacted" in line
    assert "model=redacted" in line and "total_tokens=redacted" in line
    assert "PRIVATE" not in line and "sk-" not in line and "\n" not in line


async def test_activity_context_is_isolated_across_parallel_requests_and_child_tasks(caplog):
    caplog.set_level(logging.INFO)
    entered, release = asyncio.Event(), asyncio.Event()

    async def child(tool):
        await release.wait()
        log.activity("tool", tool=tool, phase="started")

    async def request(tool, first=False):
        token = log.begin_request()
        try:
            log.activity("request", tool=tool, phase="received")
            task = asyncio.create_task(child(tool))
            if first:
                entered.set()
            await task
        finally:
            log.end_request(token)

    first = asyncio.create_task(request("first", True))
    await entered.wait()
    second = asyncio.create_task(request("second"))
    release.set()
    await asyncio.gather(first, second)
    lines = [dict(field.split("=", 1) for field in record.getMessage().split())
             for record in caplog.records if record.name == log._ACTIVITY_LOGGER.name]
    first_ids = {item["request"] for item in lines if item.get("tool") == "first"}
    second_ids = {item["request"] for item in lines if item.get("tool") == "second"}
    assert len(first_ids) == len(second_ids) == 1 and first_ids != second_ids
    log.activity("outside")
    assert "request=-" in caplog.records[-1].getMessage()


def test_activity_buffer_pagination_and_overflow():
    buffer = log.ActivityBuffer(capacity=3)
    for number in range(5):
        buffer.append(f"event=event_{number}")
    first = buffer.read(limit=2)
    assert first["reset"] is True and first["has_more"] is True
    assert [record["cursor"] for record in first["records"]] == [3, 4]
    second = buffer.read(epoch=first["epoch"], cursor=first["cursor"], limit=2)
    assert second["reset"] is False and second["has_more"] is False
    assert second["records"] == [{"cursor": 5, "line": "event=event_4"}]
    assert buffer.read(cursor=5, epoch=first["epoch"])["records"] == []


def test_activity_restart_replays_retained_records_and_handles_empty_buffer():
    before, after = log.ActivityBuffer(), log.ActivityBuffer()
    before.append("event=before")
    old = before.read()
    empty = after.read(cursor=old["cursor"], epoch=old["epoch"])
    assert empty["reset"] is True and empty["cursor"] == 0 and empty["records"] == []
    after.append("event=after")
    new = after.read(cursor=old["cursor"], epoch=old["epoch"])
    assert new["reset"] is True and new["records"] == [{"cursor": 1, "line": "event=after"}]
    assert new["epoch"] != old["epoch"]


def test_activity_buffer_bounds_record_length_and_returns_copies():
    buffer = log.ActivityBuffer()
    buffer.append("x" * 5000)
    first = buffer.read()
    assert len(first["records"][0]["line"]) == 2048
    first["records"][0]["line"] = "modified"
    assert buffer.read()["records"][0]["line"] == "x" * 2048


def test_activity_buffer_serializes_executor_threads():
    buffer = log.ActivityBuffer()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda number: buffer.append(f"event=event_{number}"), range(100)))
    records = buffer.read()["records"]
    assert [record["cursor"] for record in records] == list(range(1, 101))
    assert len({record["line"] for record in records}) == 100


@pytest.mark.parametrize("values", [{"cursor": -1}, {"cursor": True}, {"cursor": 2**63},
                                    {"limit": 0}, {"limit": 201}, {"limit": True}])
def test_activity_buffer_rejects_invalid_cursors_and_page_limits(values):
    with pytest.raises(ValueError):
        log.ActivityBuffer().read(**values)


@pytest.fixture
def detailed_trace(monkeypatch):
    monkeypatch.setattr(log, "_TRACE_ENABLED", False)
    monkeypatch.setattr(log, "_TRACE_BUFFER", log.ActivityBuffer(line_limit=8192))
    monkeypatch.setattr(log, "_ACTIVITY_BUFFER", log.ActivityBuffer())
    log.configure_trace(enabled=True)


def test_trace_is_opt_in_and_never_enters_default_activity(monkeypatch, caplog):
    monkeypatch.setattr(log, "_TRACE_ENABLED", False)
    monkeypatch.setattr(log, "_ACTIVITY_BUFFER", log.ActivityBuffer())
    caplog.set_level(logging.INFO)
    log.trace_detail("tool_input", payload={"entity_id": "light.example"})
    assert not caplog.records
    log.configure_trace(enabled=True)
    try:
        log.activity("tool", phase="started")
        log.trace_detail("tool_input", payload={"entity_id": "light.example"})
        assert len(log.read_activity()["records"]) == 1
        records = log.read_activity(detail=True)["records"]
        assert len(records) == 2 and "light.example" in records[1]["line"]
        assert log.is_trace_line(records[1]["line"])
        log.configure_trace(enabled=False)
        assert "light.example" not in str(log.read_activity(detail=True))
        log.configure_trace(enabled=True)
        assert log.read_activity(detail=True)["records"] == []
    finally:
        log.configure_trace(enabled=False)


def test_trace_redacts_nested_credentials_encoded_json_and_media(detailed_trace, caplog):
    caplog.set_level(logging.INFO)
    payload = {"entity_id": "light.example", "brightness_pct": 40,
               "nested": {"client_secret": "SECRET_1", "Authorization": "SECRET_2",
                          "X-Plex-Token": "SECRET_3", "audio": "SECRET_4"},
               "encoded": json.dumps({"api_key": "SECRET_5", "token": "SECRET_6"}),
               "text": "password=SECRET_7 Bearer SECRET_8 sk-private-token https://host/?token=SECRET_9",
               "jwt": "eyJheader.payload.signature", "instructions": "SECRET_10"}
    log.trace_detail("tool_output", payload=payload)
    line = caplog.records[-1].getMessage()
    assert "light.example" in line and '"brightness_pct":40' in line
    assert "SECRET_" not in line and "eyJheader" not in line
    assert log.is_trace_line(line)
    assert payload["nested"]["client_secret"] == "SECRET_1"


@pytest.mark.parametrize("payload", [
    "multiline\nvalue\rwith\tcontrol", "x" * 100000,
    {"items": [{"entity_id": "light.example", "value": "x" * 2048}] * 30},
    {"field_" + str(i): "quote\"\\" * 900 for i in range(100)},
    float("nan"), 10**1000,
])
def test_trace_bounds_payloads_and_relays_its_own_output(detailed_trace, payload):
    log.trace_detail("tool_output", payload=payload)
    line = log.read_activity(detail=True)["records"][-1]["line"]
    assert len(line) <= 8192 and "\n" not in line and "\r" not in line
    assert log.is_trace_line(line)
    json.loads(line.partition(" payload=")[2])


def test_trace_handles_cycles_and_private_objects_without_repr(detailed_trace):
    class PrivateObject:
        def __repr__(self):
            raise AssertionError("Do not inspect object representations")

    payload = {"opaque": PrivateObject()}
    payload["cycle"] = payload
    log.trace_detail("tool_output", payload=payload)
    line = log.read_activity(detail=True)["records"][-1]["line"]
    assert "[unsupported]" in line and "[truncated]" in line
    assert log.is_trace_line(line)


def test_large_metadata_keeps_detail_replay_valid_and_capture_time_trusted(detailed_trace, monkeypatch):
    monkeypatch.setattr(log.time, "time", lambda: 1234.5)
    fields = {name: "x" * 100 for name in log._FIELD_NAMES if name not in {"device", "function_names"}}
    fields["captured_at_ms"] = 9999
    log.trace_detail("tool_output", payload={"result": "available"}, **fields)
    line = log.read_activity(detail=True)["records"][-1]["line"]
    assert "captured_at_ms=1234500" in line and "captured_at_ms=9999" not in line
    assert log.is_trace_line(line)


@pytest.mark.parametrize("line", [
    'event=tool request=- payload={"api_key":"PRIVATE"}',
    'event=tool request=- payload={"text":"Bearer PRIVATE"}',
    'event=tool request=- payload={"text":"https://host/?token=PRIVATE"}',
    'event=tool request=- payload={"value":NaN}',
    'event=tool request=- payload={"value":1}\nforged=true',
    'event=tool request=- unknown=field payload={}',
    'event=tool request=- device=room_name payload={}',
])
def test_trace_relay_rejects_unsanitized_or_forged_payloads(line):
    assert not log.is_trace_line(line)


async def test_detail_context_matches_activity_and_isolates_parallel_jobs(detailed_trace):
    async def work(session, job):
        request = log.begin_request()
        context = log.begin_job(session, job)
        try:
            await asyncio.sleep(0)
            log.activity("tool", phase="started")
            log.trace_detail("tool_input", payload={"query": "test"})
            assert log.job_correlation() == (session, job)
        finally:
            log.end_job(context)
            log.end_request(request)

    await asyncio.gather(work("a" * 10, "b" * 10), work("c" * 10, "d" * 10))
    records = log.read_activity(detail=True)["records"]
    for session in ("a" * 10, "c" * 10):
        matching = [record["line"] for record in records if f"session={session}" in record["line"]]
        assert len(matching) == 2
        assert len({line.split("request=")[1].split()[0] for line in matching}) == 1
    assert log.job_correlation() is None
