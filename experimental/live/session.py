"""Live event handling and background jobs, independent of audio playback."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable

from .backend import BackendError
from utils.logging import activity, begin_job, end_job

LOG = logging.getLogger(__name__ + ".activity")
LOG.setLevel(logging.INFO)


def commentary_chunks(text: str):
    """Stay below 500 tokens even with non-English text, without a tokenizer dependency."""
    chunk = ""
    for char in text:
        # Byte-level BPE needs at most one token per UTF-8 byte. Leave ample margin.
        if len((chunk + char).encode("utf-8")) > 450:
            yield chunk
            chunk = ""
        chunk += char
    if chunk:
        yield chunk


@dataclass
class Job:
    id: str
    status: str = "waiting_for_context"
    request: str = ""
    result: str = ""
    duration_ms: int | None = None


class LiveSession:
    def __init__(self, live_id: str, send: Callable[[dict], Awaitable], backend,
                 settle_seconds: float = 0.8, context_timeout: float = 8):
        self.id = uuid.uuid4().hex
        self.live_id, self.send, self.backend = live_id, send, backend
        self.state, self.error = "connecting", None
        self.usage, self.finalized = {}, False
        self.created_at = self.last_activity = time.monotonic()
        self.playback_until = self.created_at
        self.last_input_at = 0.0
        self.transcripts: deque[dict] = deque(maxlen=120)
        self.jobs: dict[str, Job] = {}
        self.conversation_id = None
        self.settle_seconds, self.context_timeout = settle_seconds, context_timeout
        self._pending_text = ""
        self._event_ids: set[str] = set()
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10)
        self._closed = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self._close_lock = asyncio.Lock()

    def mark_activity(self, at=None):
        """Preserve the latest speech/work time, including queued audible playback."""
        self.last_activity = max(self.last_activity, time.monotonic() if at is None else at)

    def mark_playback(self, duration_seconds, *, audible=True):
        """Estimate speaker completion without treating generated silence as speech."""
        self.playback_until = max(time.monotonic(), self.playback_until) + duration_seconds
        if audible:
            self.mark_activity(self.playback_until)

    def is_idle(self, now, timeout):
        """Only start idle grace after speech playback and all queued work finish."""
        busy = any(job.status in ("waiting_for_context", "running") for job in self.jobs.values())
        busy = busy or (self._worker is not None and not self._worker.done())
        return not busy and now - self.last_activity >= timeout

    def log_job(self, job, phase, **fields):
        # Upstream delegation identifiers are opaque input; hash before logging.
        activity("delegation", logger=LOG, session=self.id[:10],
                 job=hashlib.sha256(str(job.id).encode()).hexdigest()[:10],
                 phase=phase, status=job.status, **fields)

    def snapshot(self):
        return {"state": self.state, "error": self.error, "finalized": self.finalized,
                "seconds": self.usage.get("seconds", 0), "usage": self.usage,
                "transcripts": list(self.transcripts),
                "tasks": [asdict(job) for job in self.jobs.values()]}

    async def receive(self, event: dict):
        event_id = event.get("event_id")
        if event_id and event_id in self._event_ids:
            return
        if event_id:
            self._event_ids.add(event_id)
        kind = event.get("type")
        if kind == "session.started":
            self.state = "active"
            self.mark_activity()
        elif kind in ("session.input_transcript.delta", "session.output_transcript.delta"):
            delta = event.get("delta", "")
            if not isinstance(delta, str) or not delta:
                return
            role = "user" if kind == "session.input_transcript.delta" else "assistant"
            self.transcripts.append({"role": role, "text": delta,
                                     "start_ms": event.get("start_ms"), "end_ms": event.get("end_ms")})
            now = time.monotonic()
            self.mark_activity(now)
            if role == "user":
                self.last_input_at = now
                self._pending_text += delta  # Preserve fragments exactly, including whitespace.
                if len(self._pending_text) > 16000:
                    self.fail("Request is too long. End this session and start a shorter request.")
        elif kind == "session.delegation.created" and self.state not in ("closing", "closed", "error"):
            delegation = event.get("delegation", {})
            job_id = delegation.get("id")
            if delegation.get("target") != "client" or not job_id or job_id in self.jobs:
                return
            if len(self.jobs) >= 50 or self._queue.full():
                await self.append("session.commentary.append", job_id,
                                  "The test session has too many pending tasks. Please start a new session.")
                return
            self.jobs[job_id] = Job(job_id)
            self.log_job(self.jobs[job_id], "queued")
            self._queue.put_nowait(job_id)
            self.mark_activity()
            if self._worker is None or self._worker.done():
                self._worker = asyncio.create_task(self._run_jobs())
        elif kind in ("session.usage.updated", "session.closed"):
            self.usage = event.get("usage", self.usage)  # Cumulative, never sum snapshots.
            if kind == "session.closed":
                self.finalized, self.state = True, "closed"
                self._closed.set()
                self._skip_queued()
        elif kind == "error":
            self.fail("GPT-Live rejected a session event. See the API event code in the server log.")

    def fail(self, message):
        self.error, self.state = message, "error"
        self._skip_queued()

    async def append(self, kind, job_id, content):
        if self.state in ("closing", "closed", "error"):
            return
        for chunk in commentary_chunks(content):
            try:
                await self.send({"type": kind, "event_id": uuid.uuid4().hex,
                                 "delegation_id": job_id, "content": chunk})
            except Exception:
                self.fail("Backend update could not be delivered to the live conversation.")
                return

    def _history(self):
        # Retain recent role-labelled context without sending an entire long session every turn.
        history, size = [], 0
        for item in reversed(self.transcripts):
            size += len(item["text"])
            if size > 8000:
                break
            history.append(dict(item))
        return list(reversed(history))

    async def _run_jobs(self):
        while not self._queue.empty():
            job = self.jobs[await self._queue.get()]
            token = begin_job(self.id[:10], hashlib.sha256(str(job.id).encode()).hexdigest()[:10])
            job_started = time.monotonic()
            try:
                if self.state in ("closing", "closed", "error"):
                    job.status = "not_started"
                    continue
                # Delegation is the trigger. A debounce alone NEVER starts backend work.
                deadline = time.monotonic() + self.context_timeout
                while time.monotonic() < deadline:
                    if self._pending_text.strip() and time.monotonic() - self.last_input_at >= self.settle_seconds:
                        break
                    await asyncio.sleep(0.05)
                if self.state in ("closing", "closed", "error"):
                    job.status = "not_started"
                    continue
                if not self._pending_text.strip() or time.monotonic() - self.last_input_at < self.settle_seconds:
                    job.status = "needs_context"
                    self.mark_activity()
                    await self.append("session.commentary.append", job.id,
                                      "I don't have a clear request yet. Please repeat what you would like me to do.")
                    continue
                # If multiple notices arrived while another job ran, act once on the latest context.
                if not self._queue.empty():
                    job.status = "superseded"
                    continue
                job.request, self._pending_text = self._pending_text.strip(), ""
                history = self._history()
                job.status = "running"
                started = time.monotonic()
                self.log_job(job, "started")
                try:
                    result = await self.backend.execute(job.request, history, self.conversation_id)
                    self.conversation_id = result.conversation_id or self.conversation_id
                    job.result, job.status = result.text, "completed"
                except BackendError as error:
                    job.result, job.status = str(error), "failed"
                    if error.uncertain:
                        self.fail(job.result)
                except Exception:
                    job.result, job.status = "Backend failed. Device actions have not been confirmed.", "failed"
                    self.fail(job.result)
                finally:
                    job.duration_ms = round((time.monotonic() - started) * 1000)
                    self.mark_activity()
                # A later delegation may correct this one. Keep the old result visible, but quiet.
                kind = "session.thinking.append" if not self._queue.empty() else "session.commentary.append"
                report = job.result if len(job.result) <= 4000 else job.result[:3500] + "\n[Result truncated; ask a narrower follow-up for more detail.]"
                await self.append(kind, job.id, report)
            finally:
                # Success, ordinary failure and clarification all get fresh grace.
                self.mark_activity()
                self._queue.task_done()
                try:
                    self.log_job(job, "finished", elapsed_ms=round((time.monotonic() - job_started) * 1000))
                finally:
                    end_job(token)

    def _skip_queued(self):
        while not self._queue.empty():
            job = self.jobs[self._queue.get_nowait()]
            job.status = "not_started"
            self.log_job(job, "finished")
            self._queue.task_done()

    async def close(self, timeout=12):
        async with self._close_lock:
            if self.finalized:
                return
            self.state = "closing"
            self._skip_queued()
            try:
                await self.send({"type": "session.close"})
                await asyncio.wait_for(self._closed.wait(), timeout)
            except (Exception, asyncio.TimeoutError):
                self.state = "error"
                self.error = "Session finalization was not confirmed; final usage is unknown."
            # An in-flight HA HTTP request is allowed to finish. Closing audio isn't tool cancellation.

    async def drain(self):
        if self._worker:
            await self._worker
