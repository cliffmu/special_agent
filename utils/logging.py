import logging
import json
import math
import re
import time
import uuid
from hashlib import sha256
from collections import deque
from contextvars import ContextVar
from threading import Lock

from .log_presentation import format_activity_line

_LOGGER = logging.getLogger("custom_components.special_agent")
_PLACEHOLDER_RE = re.compile(r"%\([^)]+\)|%[sdifr]")
_ACTIVITY_LOGGER = logging.getLogger("custom_components.special_agent.activity")
# Enable only concise activity records; the parent retains HA's configured level.
_ACTIVITY_LOGGER.setLevel(logging.INFO)
_TRACE_LOGGER = logging.getLogger("custom_components.special_agent.trace")
_TRACE_LOGGER.setLevel(logging.INFO)
_TRACE_ENABLED = False
_REQUEST_ID = ContextVar("special_agent_activity_request", default="-")
_JOB_CONTEXT = ContextVar("special_agent_activity_job", default=None)
_DEVICE_CONTEXT = ContextVar("special_agent_activity_device", default=None)
_FIELD_NAMES = frozenset({
    "phase", "status", "source", "model", "tool", "backend", "error_type",
    "elapsed_ms", "http_status", "iteration", "attempt", "tools", "tool_count",
    "input_tokens", "output_tokens", "cached_tokens", "total_tokens", "session", "job",
    "effort", "requested_tier", "effective_tier", "function_count", "function_names",
    "verification", "verified_steps", "completed_steps", "total_steps", "device",
    "route", "call_id", "response_id", "batch", "mode", "message_count", "decision",
    "reasoning_count", "web_search_count", "reason", "remaining", "prompt_kind", "detail",
    "captured_at_ms", "start_ms", "end_ms",
})
_ATOM = re.compile(r"[A-Za-z0-9_.:-]{1,100}\Z")


class ActivityBuffer:
    """Bounded, thread-safe replay of already-sanitized activity records."""

    def __init__(self, capacity=512, line_limit=2048):
        self.epoch = uuid.uuid4().hex
        self._records = deque(maxlen=capacity)
        self._cursor = 0
        self._lock = Lock()
        self._line_limit = line_limit

    def append(self, line):
        with self._lock:
            self._cursor += 1
            self._records.append({"cursor": self._cursor, "line": line[:self._line_limit]})

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
_TRACE_BUFFER = ActivityBuffer(line_limit=8192)


def read_activity(*, cursor=0, epoch=None, limit=200, detail=False):
    """Details require explicit opt-in at both capture and authenticated reader."""
    buffer = _TRACE_BUFFER if detail and _TRACE_ENABLED else _ACTIVITY_BUFFER
    return buffer.read(cursor=cursor, epoch=epoch, limit=limit)


def configure_trace(*, enabled=False):
    """Enable payload diagnostics; erase retained details when disabling."""
    global _TRACE_ENABLED, _TRACE_BUFFER
    enabled = enabled is True
    if enabled != _TRACE_ENABLED:
        _TRACE_BUFFER = ActivityBuffer(line_limit=8192)
    _TRACE_ENABLED = enabled


def trace_enabled():
    return _TRACE_ENABLED


def begin_request():
    """Start an independent request; asyncio child tool tasks inherit its ID."""
    return _REQUEST_ID.set(uuid.uuid4().hex[:10])


def end_request(token):
    _REQUEST_ID.reset(token)


def device_correlation(device_id):
    """Stable correlation without exposing device IDs or room/device names."""
    return sha256(device_id.encode()).hexdigest()[:10] if type(device_id) is str and device_id else None


def begin_device(device_id):
    return _DEVICE_CONTEXT.set(device_correlation(device_id))


def end_device(token):
    _DEVICE_CONTEXT.reset(token)


def begin_job(session_id, job_id):
    """Bind already-safe local IDs for a Live job and its HA adapter calls."""
    return _JOB_CONTEXT.set((session_id, job_id))


def end_job(token):
    _JOB_CONTEXT.reset(token)


def job_correlation():
    """Return the local/hashed Live IDs to forward across the HA HTTP boundary."""
    return _JOB_CONTEXT.get()


def _activity_value(value):
    # Never stringify containers/objects, multiline text, URLs or API credentials.
    if type(value) is bool:
        return str(value).lower()
    if type(value) in (int, float) and abs(value) <= 10**15 and math.isfinite(value):
        return str(value)
    if type(value) is str and _ATOM.fullmatch(value) and not value.startswith(("sk-", "sk_")):
        return value
    return "redacted"


def _activity_line(event, safe_values):
    parts = ["event=" + _activity_value(event), "request=" + _REQUEST_ID.get()]
    parts.append("captured_at_ms=" + str(round(time.time() * 1000)))
    device = _DEVICE_CONTEXT.get()
    if device is not None and "device" not in safe_values:
        parts.append("device=" + device)
    job_context = _JOB_CONTEXT.get()
    if job_context is not None:
        parts.extend(f"{name}={_activity_value(value)}" for name, value in zip(("session", "job"), job_context)
                     if name not in safe_values)
    for name, value in safe_values.items():
        if name not in _FIELD_NAMES or name == "captured_at_ms":
            continue
        if name == "device":
            formatted = value if type(value) is str and re.fullmatch(r"[0-9a-f]{10}", value) else "redacted"
        elif name == "function_names" and type(value) in (list, tuple):
            formatted = ",".join(_activity_value(item) for item in value[:10]) or "none"
        else:
            formatted = _activity_value(value)
        parts.append(f"{name}={formatted}")
    line = " ".join(parts)
    # Keep whole fields so replay validation never sees a half-written value.
    return line[:2048].rsplit(" ", 1)[0] if len(line) > 2048 else line


def activity(event, *, logger=None, **safe_values):
    """Emit identifiers/counts only; unknown fields and payloads are discarded."""
    line = _activity_line(event, safe_values)
    _ACTIVITY_BUFFER.append(line)
    if _TRACE_ENABLED:
        _TRACE_BUFFER.append(line)
    emit_activity_line(line, logger=logger if logger is not None else _ACTIVITY_LOGGER)


def emit_activity_line(line, *, logger):
    """Present a sanitized record without changing its machine-readable replay."""
    logger.info("%s", format_activity_line(line), extra={"special_agent_activity": line})


_SECRET_KEY = re.compile(
    r"(?i)(?:api[_-]?key|(?:access|refresh|auth|device|supervisor)[_-]?token|"
    r"token(?!s)|authorization|password|passwd|secret|credential|cookie|private[_-]?key|"
    r"encrypted_content|raw_reasoning|audio|image|base64)"
)
_SECRET_TEXT = re.compile(
    r"(?i)\b(?:sk[-_][a-z0-9_-]+|Bearer\s+[^\s,;\"']+|"
    r"eyJ[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+)|"
    r"(?:https?|wss?)://[^\s<>\"']+"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|(?:access|refresh|auth|device|supervisor)[_-]?token|"
    r"token|authorization|password|passwd|secret|cookie)\b[\"']?\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)

# Transcript display blocks can end in the middle of a credential. Match open
# quoted assignments and token prefixes before a later block completes them.
_OPEN_SECRET_QUOTE = re.compile(
    r"(?is)\b(api[_-]?key|(?:access|refresh|auth|device|supervisor)[_-]?token|"
    r"token|authorization|password|passwd|secret|cookie)\b[\"']?\s*[:=]\s*"
    r"([\"'])(?:(?!\2).)*\Z"
)
_FRAGMENT_SECRET_TEXT = re.compile(
    r"(?i)\b(?:sk[-_][a-z0-9_-]*|Bearer\s+[^\s,;\"']*|"
    r"eyJ[a-z0-9_.-]*|(?:https?|wss?)://[^\s<>\"']*)"
)


class TraceTextRedactor:
    """Keep bounded credential context across one speaker's display blocks.

    The retained tail is diagnostic state only. An unfinished sensitive value
    uses a short synthetic prefix, so arbitrarily long secrets remain masked
    without retaining them or changing application transcripts.
    """

    def __init__(self):
        self._context = ""

    @staticmethod
    def _redact(text):
        text = _OPEN_SECRET_QUOTE.sub(lambda match: match[1] + "=[redacted]", text)
        text = _FRAGMENT_SECRET_TEXT.sub("[redacted]", text)
        return _SECRET_ASSIGNMENT.sub(lambda match: match[1] + "=[redacted]", text)

    @staticmethod
    def _continuation_context(text):
        opened = _OPEN_SECRET_QUOTE.search(text)
        if opened:
            return opened[1] + "=" + opened[2] + "redacted"
        assignments = list(_SECRET_ASSIGNMENT.finditer(text))
        if assignments and assignments[-1].end() == len(text):
            assignment = assignments[-1]
            value = re.split(r"[:=]", assignment[0], maxsplit=1)[1].lstrip()
            if not (len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]):
                return assignment[1] + "=redacted"
        tokens = list(_FRAGMENT_SECRET_TEXT.finditer(text))
        if tokens and tokens[-1].end() == len(text):
            token = tokens[-1][0].lower()
            if token.startswith("bearer"):
                return "Bearer " if token.strip() == "bearer" else "Bearer redacted"
            if token.startswith("sk"):
                return "sk-redacted"
            if token.startswith("eyj"):
                return "eyJredacted.redacted.redacted"
            return "https://redacted.invalid/"
        # Keep split labels/schemes recognizable, including a long whitespace
        # gap between a secret label and its assignment separator.
        return re.sub(r"\s+", " ", text)[-256:]

    def redact(self, text):
        previous = self._redact(self._context)
        combined = self._context + text
        sanitized = self._redact(combined)
        self._context = self._continuation_context(combined)
        shared = 0
        while shared < min(len(previous), len(sanitized)) and previous[shared] == sanitized[shared]:
            shared += 1
        return sanitized[shared:] or ("[redacted]" if text else "")


def _trace_payload(value, depth=0, budget=None):
    """Copy bounded JSON primitives without invoking application object reprs."""
    if budget is None:
        budget = [150]
    budget[0] -= 1
    if depth > 6 or budget[0] < 0:
        return "[truncated]"
    if value is None or type(value) is bool:
        return value
    if type(value) in (int, float):
        return value if abs(value) <= 10**15 and math.isfinite(value) else "[redacted]"
    if type(value) is str:
        # Handle JSON-encoded tool outputs as objects, so secret keys still count.
        if len(value) <= 65536 and value.lstrip().startswith(("{", "[")):
            try:
                return _trace_payload(json.loads(value), depth + 1, budget)
            except (ValueError, RecursionError):
                pass
        bounded = value[:65536]
        bounded = _SECRET_TEXT.sub("[redacted]", bounded)
        bounded = _SECRET_ASSIGNMENT.sub(lambda m: m.group(1) + "=[redacted]", bounded)
        return bounded[:2048] + ("[truncated]" if len(bounded) > 2048 or len(value) > 65536 else "")
    if type(value) is dict:
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 30 or budget[0] <= 0:
                result["_truncated"] = True
                break
            if type(key) is not str:
                continue
            label = _SECRET_TEXT.sub("[redacted]", key[:100])
            result[label] = "[redacted]" if _SECRET_KEY.search(key) or key.lower() in {
                "token", "key", "headers", "instructions", "system_prompt",
            } else _trace_payload(item, depth + 1, budget)
        return result
    if type(value) in (list, tuple):
        result = [_trace_payload(item, depth + 1, budget) for item in value[:20]]
        if len(value) > 20:
            result.append("[truncated]")
        return result
    return "[unsupported]"


def _encode_trace_payload(payload):
    encoded = json.dumps(_trace_payload(payload), ensure_ascii=True, separators=(",", ":"))
    if len(encoded) > 4096:
        preview = encoded[:1800]
        encoded = json.dumps({"_truncated": True, "preview": preview}, ensure_ascii=True, separators=(",", ":"))
    return encoded


def trace_detail(event, *, payload, logger=None, **safe_values):
    """Opt-in inputs/results, with credential/media redaction and explicit bounds.

    Diagnostic content can still contain personal conversations and device names.
    Never pass full session configuration, audio, credentials, or raw reasoning.
    """
    if not _TRACE_ENABLED:
        return
    line = _activity_line(event, safe_values) + " payload=" + _encode_trace_payload(payload)
    _TRACE_BUFFER.append(line)
    emit_activity_line(line, logger=logger if logger is not None else _TRACE_LOGGER)


def is_trace_line(line):
    """Validate opt-in relay records again at the bridge boundary."""
    if type(line) is not str or not 1 <= len(line) <= 8192 or "\n" in line or "\r" in line:
        return False
    prefix, separator, payload = line.partition(" payload=")
    if not separator or len(payload) > 4096:
        return False
    fields = prefix.split(" ")
    if len(fields) < 2 or not fields[0].startswith("event=") or not fields[1].startswith("request="):
        return False
    names = set()
    for field in fields:
        name, sep, value = field.partition("=")
        if not sep or name not in _FIELD_NAMES | {"event", "request"} or name in names:
            return False
        names.add(name)
        values = value.split(",") if name == "function_names" else [value]
        if not 1 <= len(values) <= 10 or any(_activity_value(atom) != atom for atom in values):
            return False
        if name == "device" and not re.fullmatch(r"[0-9a-f]{10}", value):
            return False
    try:
        parsed = json.loads(payload)
        return _encode_trace_payload(parsed) == payload
    except (ValueError, RecursionError):
        return False


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
