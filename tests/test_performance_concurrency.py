"""CSV batches remain distinct when requests finish during a background flush."""

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import csv
import threading

import pytest

from special_agent.utils import performance


@pytest.fixture(autouse=True)
def reset_records(monkeypatch):
    monkeypatch.setattr(performance, "_enabled", True)
    performance.clear_records()
    yield
    performance.clear_records()


def record(identity):
    return performance.TimingRecord(request_id=identity, session_id="session-" + identity,
                                   operation="tool", start_time=1, end_time=2, duration_s=1)


def rows(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_concurrent_flushes_preserve_records_added_during_csv_io(tmp_path, monkeypatch):
    path = tmp_path / "timings.csv"
    performance.append_record(record("request-a"))
    writing, release = threading.Event(), threading.Event()
    write_records = performance._write_csv_records

    def paused_write(batch, output_path):
        if batch[0].request_id == "request-a":
            writing.set()
            assert release.wait(2)
        write_records(batch, output_path)

    monkeypatch.setattr(performance, "_write_csv_records", paused_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(performance.write_csv, path)
        second = None
        try:
            assert writing.wait(1)
            performance.append_record(record("request-b"))
            assert [item.request_id for item in performance.get_records()] == ["request-b"]
            second = executor.submit(performance.write_csv, path)
            with pytest.raises(FutureTimeout):
                second.result(timeout=0.05)
        finally:
            release.set()
            first.result(timeout=1)
            if second is not None:
                second.result(timeout=1)
    assert [(row["request_id"], row["session_id"]) for row in rows(path)] == [
        ("request-a", "session-request-a"), ("request-b", "session-request-b")]
    assert performance.get_records() == []


def test_failed_flush_restores_batch_without_partial_rows_or_duplicate_retry(tmp_path, monkeypatch):
    path = tmp_path / "timings.csv"
    performance.append_record(record("existing"))
    performance.write_csv(path)
    original = path.read_bytes()
    performance.append_record(record("request-a"))
    replace = performance.os.replace

    def fail_replace(*args):
        performance.append_record(record("request-b"))
        raise OSError("test replacement failure")

    monkeypatch.setattr(performance.os, "replace", fail_replace)
    with pytest.raises(OSError):
        performance.write_csv(path)
    assert path.read_bytes() == original
    assert [item.request_id for item in performance.get_records()] == ["request-a", "request-b"]
    assert list(tmp_path.iterdir()) == [path]

    monkeypatch.setattr(performance.os, "replace", replace)
    performance.write_csv(path)
    assert [row["request_id"] for row in rows(path)] == ["existing", "request-a", "request-b"]
    assert performance.get_records() == []


async def test_overlapping_requests_keep_session_and_prompt_metadata_together():
    both_started, release = asyncio.Event(), asyncio.Event()
    running, identities = [], {}

    async def request(name):
        async with performance.track_request("voice", metadata={"prompt": name}) as request_id:
            identities[name] = request_id
            performance.set_session_id("session-" + name)
            running.append(name)
            if len(running) == 2:
                both_started.set()
            async with performance.track_operation("tool", metadata={"source": name}):
                await release.wait()

    tasks = [asyncio.create_task(request(name)) for name in ("first", "second")]
    try:
        await asyncio.wait_for(both_started.wait(), 1)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
    records = performance.get_records()
    assert len(records) == 4
    for name, request_id in identities.items():
        own = [item for item in records if item.request_id == request_id]
        assert len(own) == 2 and {item.session_id for item in own} == {"session-" + name}
        assert next(item for item in own if item.operation == "voice").metadata["prompt"] == name
        assert next(item for item in own if item.operation == "tool").metadata["source"] == name
