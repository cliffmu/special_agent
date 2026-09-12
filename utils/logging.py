import logging
import math
import re
import uuid
from collections import deque
from contextvars import ContextVar
from threading import Lock

_LOGGER = logging.getLogger("custom_components.special_agent")
_PLACEHOLDER_RE = re.compile(r"%\([^)]+\)|%[sdifr]")
_ACTIVITY_LOGGER = logging.getLogger("custom_components.special_agent.activity")
# Enable only concise activity records; the parent retains HA's configured level.
_ACTIVITY_LOGGER.setLevel(logging.INFO)
_REQUEST_ID = ContextVar("special_agent_activity_request", default="-")
_JOB_CONTEXT = ContextVar("special_agent_activity_job", default=None)
_FIELD_NAMES = frozenset({
    "phase", "status", "source", "model", "tool", "backend", "error_type",
    "elapsed_ms", "http_status", "iteration", "attempt", "tools", "tool_count",
    "input_tokens", "output_tokens", "cached_tokens", "total_tokens", "session", "job",
    "effort", "requested_tier", "effective_tier", "function_count", "function_names",
    "verification", "verified_steps", "completed_steps", "total_steps",
})
_ATOM = re.compile(r"[A-Za-z0-9_.:-]{1,100}\Z")


class ActivityBuffer:
    """Bounded, thread-safe replay of already-sanitized activity records."""

    def __init__(self, capacity=512):
        self.epoch = uuid.uuid4().hex
        self._records = deque(maxlen=capacity)
        self._cursor = 0
        self._lock = Lock()

    def append(self, line):
        with self._lock:
            self._cursor += 1
            self._records.append({"cursor": self._cursor, "line": line[:2048]})

    def read(self, *, cursor=0, epoch=None, limit=200):
        if type(cursor) is not int or not 0 <= cursor <= 2**63 - 1:
            raise ValueError("Invalid activity cursor")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Activity limit must be between 1 and 200")
        with self._lock:
            oldest = self._records[0]["cursor"] if self._records else self._cursor + 1
            reset = (epoch is not None and epoch != self.epoch) or cursor < oldest - 1 or cursor > self._cursor
            if reset:
                cursor = oldest - 1
            records = [dict(record) for record in self._records if record["cursor"] > cursor][:limit]
            next_cursor = records[-1]["cursor"] if records else self._cursor
            return {"epoch": self.epoch, "cursor": next_cursor, "reset": reset,
                    "has_more": next_cursor < self._cursor, "records": records}


_ACTIVITY_BUFFER = ActivityBuffer()


def read_activity(*, cursor=0, epoch=None, limit=200):
    """Read safe activity only; ordinary Core logs never enter this buffer."""
    return _ACTIVITY_BUFFER.read(cursor=cursor, epoch=epoch, limit=limit)


def begin_request():
    """Start an independent request; asyncio child tool tasks inherit its ID."""
    return _REQUEST_ID.set(uuid.uuid4().hex[:10])


def end_request(token):
    _REQUEST_ID.reset(token)


def begin_job(session_id, job_id):
    """Bind already-safe local IDs for a Live job and its HA adapter calls."""
    return _JOB_CONTEXT.set((session_id, job_id))


def end_job(token):
    _JOB_CONTEXT.reset(token)


def _activity_value(value):
    # Never stringify containers/objects, multiline text, URLs or API credentials.
    if type(value) is bool:
        return str(value).lower()
    if type(value) in (int, float) and abs(value) <= 10**15 and math.isfinite(value):
        return str(value)
    if type(value) is str and _ATOM.fullmatch(value) and not value.startswith(("sk-", "sk_")):
        return value
    return "redacted"


def activity(event, *, logger=None, **safe_values):
    """Emit one bounded line of identifiers/counts, never application payloads.

    Callers supply fixed event/status labels and trusted model/registered-tool
    names. Unknown fields (prompt, arguments, result, credentials, etc.) are
    discarded. Live uses its own logger and hashed/local correlation IDs.
    """
    parts = ["event=" + _activity_value(event), "request=" + _REQUEST_ID.get()]
    job_context = _JOB_CONTEXT.get()
    if job_context is not None:
        parts.extend(f"{name}={_activity_value(value)}" for name, value in zip(("session", "job"), job_context)
                     if name not in safe_values)
    for name, value in safe_values.items():
        if name not in _FIELD_NAMES:
            continue
        if name == "function_names" and type(value) in (list, tuple):
            formatted = ",".join(_activity_value(item) for item in value[:10]) or "none"
        else:
            formatted = _activity_value(value)
        parts.append(f"{name}={formatted}")
    line = " ".join(parts)
    _ACTIVITY_BUFFER.append(line)
    (logger if logger is not None else _ACTIVITY_LOGGER).info("%s", line)


def _safe(level, msg, *args, **kw):
    if args:
        try:  # 1️⃣ classic %-style happy path
            if _PLACEHOLDER_RE.search(msg):
                _LOGGER.log(level, msg, *args, **kw)
                return
            # 2️⃣ brace‑style ?  configure a Formatter(style='{') once
            if "{" in msg and "}" in msg:
                _LOGGER.log(level, msg.format(*args), **kw)
                return
            # 3️⃣ no placeholders → just append repr()s
            msg = f"{msg} " + " ".join(repr(a) for a in args)
        except Exception:  # paranoia; never crash caller
            msg = f"{msg} " + " ".join(repr(a) for a in args)
    _LOGGER.log(level, msg, **kw)


def debug(msg, *args, **kw):
    _safe(logging.DEBUG, msg, *args, **kw)


def info(msg, *args, **kw):
    _safe(logging.INFO, msg, *args, **kw)


def warning(msg, *args, **kw):
    _safe(logging.WARNING, msg, *args, **kw)


def error(msg, *args, **kw):
    _safe(logging.ERROR, msg, *args, **kw)
