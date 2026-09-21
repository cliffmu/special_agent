"""Readable, single-line presentation of already-sanitized activity records.

This module changes display only. Capture, redaction, validation and replay keep
their existing structured format; the original record remains to the right.
"""

import json
import re
from datetime import datetime, timezone

_MAX_RAW = 8192
_MAX_FRONT = 240
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
_FIELD = re.compile(r"[a-z_]+=[^\s=]+\Z")
_ACRONYMS = {"ha": "HA", "gpt": "GPT", "api": "API", "id": "ID", "tts": "TTS"}


def _single_line(text):
    return _CONTROL.sub(lambda match: {
        "\n": r"\n", "\r": r"\r", "\t": r"\t",
    }.get(match[0], f"\\u{ord(match[0]):04x}"), text)


def _short(text, limit=_MAX_FRONT):
    text = _single_line(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _name(value):
    return " ".join(_ACRONYMS.get(word.lower(), word.capitalize())
                    for word in value.replace("-", "_").split("_") if word)


def _words(value):
    return value.replace("_", " ").replace("-", " ")


def _value(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _preview(payload):
    if isinstance(payload, dict):
        return "; ".join(f"{key}={_value(value)}" for key, value in payload.items()) or "{}"
    return _value(payload)


def _text(payload, *keys):
    if isinstance(payload, dict):
        for key in keys:
            if key in payload and payload[key] is not None:
                return _value(payload[key])
    return _preview(payload)


def _outcome(fields, payload=None):
    status = fields.get("status", "")
    if isinstance(payload, dict):
        if payload.get("error") or payload.get("success") is False or payload.get("ok") is False:
            status = "error"
        elif isinstance(payload.get("verification"), str) and payload["verification"] in {"failed", "unverified"}:
            status = payload["verification"]
        elif isinstance(payload.get("status"), str):
            status = payload["status"]
    elif isinstance(payload, str) and payload.startswith("Error:"):
        status = "error"
    return _words(status)


def _with_outcome(summary, fields, payload=None):
    outcome = _outcome(fields, payload)
    return f"{outcome}: {summary}" if outcome and outcome not in {"ok", "completed", "returned"} else summary


def _tool_record(event, fields, payload, has_payload):
    tool = _name(fields.get("tool", "unknown"))
    phase = fields.get("phase", "")
    labels = {"tool_input": "Tool call", "tool_result": "Tool returned",
              "tool_output": "Agent observation", "tool_skipped": "Tool skipped",
              "model_tool_call": "Tool selected", "model_builtin_tool": "Tool returned",
              "web_search": "Tool activity"}
    label = labels.get(event, "Tool started" if phase == "started" else
                       "Tool skipped" if phase == "skipped" else "Tool finished")
    summary = _preview(payload) if has_payload else _words(fields.get("status", phase) or "recorded")
    if has_payload:
        summary = _with_outcome(summary, fields, payload)
    if event == "model_tool_call":
        summary = "Requested by the agent model; execution follows validation"
    elif event == "model_builtin_tool":
        summary += "; handled by the model provider"
    return f"{label} [{tool}]", summary


def _batch_record(fields):
    phase, mode = fields.get("phase", ""), fields.get("mode", "")
    count = fields.get("tool_count", fields.get("function_count", "?"))
    if phase == "planned":
        names = ", ".join(_name(name) for name in fields.get("function_names", "").split(",") if name)
        return "Agent tools planned", f"{count} requested" + (f": {names}" if names else "")
    if phase == "started":
        if mode == "none":
            return "Agent tools", "No calls passed validation"
        suffix = "; next model round waits for all results" if mode == "parallel" else "; waiting for its result"
        return "Agent waiting", f"{count} tool(s) running {_words(mode)}{suffix}"
    if phase == "progress":
        completed, total = fields.get("completed_steps", "?"), fields.get("total_steps", "?")
        tool = _name(fields.get("tool", "tool"))
        if fields.get("status") == "completed" or fields.get("remaining") == "0":
            return "Agent tools ready", f"{completed}/{total} calls finished; {tool} finished last; results can now be processed"
        return "Agent waiting", (f"{completed}/{total} calls finished ({tool}); "
                                 f"waiting for {fields.get('remaining', '?')} remaining before the next model round")
    decision = fields.get("decision", "")
    summary = f"{count} tool(s) finished"
    if decision == "return_to_model":
        summary += "; results ready for the next model round"
    elif decision == "respond_to_user":
        summary += "; a tool supplied the response or question"
    return "Agent tools finished", _with_outcome(summary, fields)


def _model_record(fields):
    phase = fields.get("phase", "")
    model = fields.get("model", "model")
    round_number = fields.get("iteration", "?")
    if phase == "sent":
        return "Agent round", f"{round_number}: asking {model}; {fields.get('tool_count', '?')} tools available"
    if phase == "failed":
        return "Agent model error", _with_outcome(fields.get("error_type", "Model request failed"), fields)
    decision = fields.get("decision")
    summary = (f"selected {fields.get('function_count', '?')} tool call(s)" if decision == "call_tools" else
               "returned text" if decision == "text_response" else "returned no action")
    return "Agent round", _with_outcome(f"{round_number}: {model} {summary}", fields)


def _loop_record(fields):
    phase = fields.get("phase", "")
    summary = {
        "started": "Starting the Python agent loop",
        "session_loaded": "Conversation history loaded",
        "retrying": "Retrying the model request",
        "continuing": "Preparing the next model round",
        "finished": "Agent loop finished",
    }.get(phase, _words(phase) or "Activity recorded")
    reason = fields.get("reason") or fields.get("decision")
    if reason:
        summary += "; " + _words(reason)
    return "Agent loop", _with_outcome(summary, fields)


def _live_record(event, fields, payload, has_payload):
    phase = fields.get("phase", "")
    if event == "live_session":
        summary = _words(phase)
        if fields.get("route") == "delegation_only":
            summary += "; Live delegates work to the agent loop"
        return "Live session", _with_outcome(summary, fields)
    if event == "live_delivery":
        mode = "background context" if fields.get("mode") == "background_context" else "commentary"
        summary = (f"{mode}: {_text(payload, 'content')}" if has_payload else
                   f"{mode} {_words(phase)}; this is delivery to Live, not confirmation of speaker playback")
        return "Live delivery", _with_outcome(summary, fields)
    if event == "ha_request":
        if has_payload:
            return "Agent context", "Delegated request and conversation context sent to Home Assistant"
        phases = {"queue_wait": "Waiting for the previous request on this device",
                  "queue_resumed": "Previous request finished; dispatch can continue",
                  "queue_cancelled": "Cancelled before dispatch; request not sent",
                  "sent": "Request sent to Home Assistant's agent loop",
                  "received": "Home Assistant replied", "failed": "Home Assistant request failed"}
        label = "Live queue" if phase.startswith("queue_") else "Live backend"
        return label, _with_outcome(phases.get(phase, _words(phase)), fields)
    if event == "delegation_request":
        return "Agent request (delegated)", _text(payload, "request")
    if event in {"delegation_result", "ha_result"}:
        return "Live result received", _with_outcome(_text(payload, "result", "speech"), fields)
    phases = {"queued": "Work queued for the agent loop",
              "context_wait": "Waiting for the user transcript to settle",
              "context_timeout": "Transcript did not settle in time; clarification needed",
              "context_ready": "Transcript ready for dispatch",
              "started": "Agent loop working; Live conversation remains active",
              "result_received": "Agent result received",
              "interrupted": "Delegation interrupted; check the recorded outcome",
              "finished": "Delegation finished"}
    return "Live delegation", _with_outcome(phases.get(phase, _words(phase)), fields)


def _describe(event, fields, payload, has_payload):
    if event in {"tool", "tool_input", "tool_result", "tool_output", "tool_skipped",
                 "model_tool_call", "model_builtin_tool", "web_search"}:
        return _tool_record(event, fields, payload, has_payload)
    if event == "tool_batch":
        return _batch_record(fields)
    if event == "model":
        return _model_record(fields)
    if event == "agent_loop":
        return _loop_record(fields)
    if event == "live_transcript":
        label = "User message [Live]" if fields.get("mode") == "user" else "Live response (transcript)"
        return label, _text(payload, "text")
    if event == "agent_request":
        text = _text(payload, "text")
        # Display the current request before the longer quoted context wrapper.
        if text.startswith("A live voice conversation has delegated the following request."):
            return "Agent request (delegated)", text.rpartition("\nCurrent request: ")[2] or text
        return "Agent request", text
    if event == "agent_tools":
        names = payload.get("tools", []) if isinstance(payload, dict) else []
        label = "Agent tools available" + (" (continued)" if fields.get("phase") == "continued" else "")
        return label, ", ".join(_name(name) for name in names if isinstance(name, str)) or "None recorded"
    if event == "agent_reasoning":
        if not has_payload or fields.get("status") == "unavailable":
            return "Agent reasoning summary", "Unavailable; the model returned no reasoning summary"
        return "Agent reasoning summary", _text(payload, "summary")
    if event in {"agent_response", "model_text"}:
        label = "Agent response" if event == "agent_response" else "Agent message"
        return label, _with_outcome(_text(payload, "text", "speak", "speech", "question"), fields)
    if event == "model_output":
        return "Agent model output", _text(payload, "text", "reasoning_summaries")
    if event in {"live_session", "live_delivery", "ha_request", "ha_result", "delegation",
                 "delegation_request", "delegation_result"}:
        return _live_record(event, fields, payload, has_payload)
    if event == "request":
        return "Agent request status", _with_outcome(_words(fields.get("phase", "recorded")), fields)
    if event == "sequence_step":
        return "Tool sequence step", _with_outcome(f"{fields.get('iteration', '?')} finished", fields)
    return _name(event) or "Log record", (_preview(payload) if has_payload else
                                           _words(fields.get("phase", "Activity recorded")))


def format_activity_line(line: str) -> str:
    """Put the action first and retain the bounded structured record to its right.

    Call after capture/relay validation. Malformed records remain inspectable but
    cannot inject extra lines or terminal control characters into the log view.
    """
    if not isinstance(line, str):
        return "Log record: Unavailable (invalid record type)"
    raw = _short(line, _MAX_RAW)
    prefix, separator, encoded = line[:_MAX_RAW].partition(" payload=")
    parts = prefix.split(" ")
    try:
        if len(line) > _MAX_RAW or not parts or not all(_FIELD.fullmatch(part) for part in parts):
            raise ValueError("Malformed fields")
        fields = dict(part.split("=", 1) for part in parts)
        if len(fields) != len(parts) or "event" not in fields or "request" not in fields:
            raise ValueError("Missing or duplicate fields")
        payload = json.loads(encoded) if separator else None
        label, summary = _describe(fields["event"], fields, payload, bool(separator))
        front = _short(f"{label}: {summary}")
    except (ValueError, TypeError, RecursionError, AttributeError):
        return "Log record: Malformed activity record | " + raw
    captured = ""
    try:
        timestamp = datetime.fromtimestamp(int(fields["captured_at_ms"]) / 1000, tz=timezone.utc)
        captured = "time=" + timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z") + " "
    except (KeyError, ValueError, OverflowError, OSError):
        pass
    return front + " | " + captured + raw
