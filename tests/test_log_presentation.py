"""The display tells the execution story while retaining inspectable raw detail."""

import json

import pytest

from utils.log_presentation import format_activity_line


def record(event, *, payload=None, **fields):
    line = f"event={event} request=abc123 captured_at_ms=1789653600123"
    line += "".join(f" {key}={value}" for key, value in fields.items())
    if payload is not None:
        line += " payload=" + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return line


@pytest.mark.parametrize(("event", "payload", "fields", "expected"), [
    ("live_transcript", {"text": "Turn on office lights how I like"}, {"mode": "user", "phase": "captured"},
     "User message [Live]: Turn on office lights how I like"),
    ("live_transcript", {"text": "Okay, I'll look into that."}, {"mode": "assistant", "phase": "captured"},
     "Live response (transcript): Okay, I'll look into that."),
    ("agent_request", {"text": "Turn on office lights"}, {}, "Agent request: Turn on office lights"),
    ("delegation_request", {"request": "office lights", "history": [{"text": "earlier text"}]}, {},
     "Agent request (delegated): office lights"),
    ("agent_tools", {"tools": ["search_devices", "search_spotify", "play_plex_media", "ha_api"]}, {},
     "Agent tools available: Search Devices, Search Spotify, Play Plex Media, HA API"),
    ("agent_reasoning", {"summary": "Checking saved scenes for the room."}, {},
     "Agent reasoning summary: Checking saved scenes for the room."),
    ("model_text", {"text": "I found your scene."}, {}, "Agent message: I found your scene."),
    ("agent_response", {"speak": "The lights are on.", "kind": "response"}, {},
     "Agent response: The lights are on."),
    ("tool_input", {"query": "office lights", "domain": "light"}, {"tool": "search_devices"},
     "Tool call [Search Devices]: query=office lights; domain=light"),
    ("tool_result", {"count": 2, "matches": ["light.desk", "light.floor"]}, {"tool": "search_devices"},
     'Tool returned [Search Devices]: count=2; matches=["light.desk","light.floor"]'),
    ("tool_output", {"count": 2}, {"tool": "search_devices"},
     "Agent observation [Search Devices]: count=2"),
])
def test_activity_starts_with_human_label_and_preserves_source(event, payload, fields, expected):
    raw = record(event, payload=payload, **fields)
    rendered = format_activity_line(raw)
    front, details = rendered.split(" | ", 1)
    assert front == expected
    assert details.startswith("time=2026-09-17T14:00:00.123Z ")
    assert details.endswith(raw)


def test_delegated_wrapper_is_inspectable_but_current_request_leads():
    text = ("A live voice conversation has delegated the following request. Follow the rules.\n"
            'Recent voice context (JSON): [{"text":"earlier"}]\nCurrent request: Make the office cozy')
    raw = record("agent_request", payload={"text": text})
    rendered = format_activity_line(raw)
    assert rendered.startswith("Agent request (delegated): Make the office cozy | ")
    assert rendered.endswith(raw)


def test_parallel_progress_says_first_return_does_not_start_next_model_round():
    started = format_activity_line(record("tool_batch", phase="started", mode="parallel", tool_count=3))
    progress = format_activity_line(record("tool_batch", phase="progress", mode="parallel",
                                          completed_steps=1, total_steps=3, remaining=2,
                                          tool="search_devices", status="waiting"))
    complete = format_activity_line(record("tool_batch", phase="progress", mode="parallel",
                                          completed_steps=3, total_steps=3, remaining=0,
                                          tool="search_spotify", status="completed"))
    assert started.startswith("Agent waiting: 3 tool(s) running parallel; next model round waits for all results |")
    assert progress.startswith("Agent waiting: 1/3 calls finished (Search Devices); waiting for 2 remaining before the next model round |")
    assert complete.startswith("Agent tools ready: 3/3 calls finished; Search Spotify finished last; results can now be processed |")


@pytest.mark.parametrize(("event", "payload", "fields", "fragment"), [
    ("tool", None, {"phase": "skipped", "tool": "prompt_user", "status": "not_parallel"},
     "Tool skipped [Prompt User]: not parallel"),
    ("tool", None, {"phase": "finished", "tool": "control_device", "status": "cancelled"},
     "Tool finished [Control Device]: cancelled"),
    ("tool_result", {"success": False, "message": "Device offline"}, {"tool": "control_device"},
     "Tool returned [Control Device]: error:"),
    ("tool_output", "Error: Service unavailable", {"tool": "control_device"},
     "Agent observation [Control Device]: error: Error: Service unavailable"),
    ("tool_batch", None, {"phase": "finished", "mode": "parallel", "status": "cancelled"},
     "Agent tools finished: cancelled:"),
    ("model", None, {"phase": "failed", "error_type": "TimeoutError", "status": "error"},
     "Agent model error: error: TimeoutError"),
    ("ha_request", None, {"phase": "queue_cancelled", "status": "not_sent"},
     "Live queue: not sent: Cancelled before dispatch; request not sent"),
])
def test_failures_skips_and_cancellation_are_not_presented_as_success(event, payload, fields, fragment):
    assert format_activity_line(record(event, payload=payload, **fields)).startswith(fragment)


def test_live_background_delivery_is_not_labeled_as_thought_or_audible_speech():
    raw = record("live_delivery", phase="sent", mode="background_context")
    rendered = format_activity_line(raw)
    front = rendered.split(" | ", 1)[0]
    assert front == "Live delivery: background context sent; this is delivery to Live, not confirmation of speaker playback"
    assert "thought" not in front.lower()
    assert "reasoning" not in front.lower()


def test_missing_reasoning_summary_is_explicit_without_fabricated_thought():
    rendered = format_activity_line(record("agent_reasoning", status="unavailable", reason="no_summary_returned"))
    assert rendered.startswith("Agent reasoning summary: Unavailable; the model returned no reasoning summary | ")


def test_model_text_is_not_claimed_as_final_response_or_live_speech():
    rendered = format_activity_line(record("model_text", payload={"text": "I will check the scene."}, decision="call_tools"))
    assert rendered.startswith("Agent message: I will check the scene. | ")
    assert "Agent response" not in rendered
    assert "Live response" not in rendered


def test_long_payload_keeps_brief_front_and_all_bounded_details_to_right():
    raw = record("tool_result", payload={"rows": [{"value": "x" * 150} for _ in range(20)]},
                 tool="search_devices", iteration=2, job="aaaa111111", call_id="bbbb222222", batch="abc123")
    rendered = format_activity_line(raw)
    front, detail = rendered.split(" | ", 1)
    assert len(front) == 240 and front.endswith("…")
    assert detail.endswith(raw)
    assert "iteration=2 job=aaaa111111 call_id=bbbb222222 batch=abc123" in detail
    assert json.dumps({"rows": [{"value": "x" * 150} for _ in range(20)]}, separators=(",", ":")) in detail


@pytest.mark.parametrize("dangerous", ["\nAgent response: spoofed", "\rspoof", "\x1b[2J", "\tspoof", "\u2028spoof", "\u2029spoof", "\u202espoof", "\x85spoof"])
def test_decoded_payload_cannot_inject_new_lines_or_terminal_controls(dangerous):
    raw = record("agent_response", payload={"text": "Real text" + dangerous})
    rendered = format_activity_line(raw)
    assert len(rendered.splitlines()) == 1
    assert dangerous not in rendered
    assert rendered.endswith(raw)


@pytest.mark.parametrize("raw", [
    "", "arbitrary legacy log", "event=tool", "event=tool request=x broken-field",
    "event=tool request=x event=model", "event=tool request=x payload={broken",
    "event=tool request=x payload=" + "[" * 3000,
    "event=tool request=x\nAgent response: spoofed", "event=tool request=x\x1b[2J",
    "x" * 100000,
])
def test_malformed_records_fail_safely_and_remain_bounded(raw):
    rendered = format_activity_line(raw)
    assert len(rendered.splitlines()) == 1
    assert "\x1b" not in rendered
    assert len(rendered) < 8500


def test_unknown_event_uses_readable_name_without_losing_detail():
    raw = record("future_runtime_event", phase="received", payload={"new_field": "future detail"})
    assert format_activity_line(raw).startswith("Future Runtime Event: new_field=future detail | ")
    assert format_activity_line(raw).endswith(raw)


def test_unusual_structured_tool_return_does_not_break_rendering():
    raw = record("tool_result", tool="control_device", payload={"verification": {"device": "pending"}})
    assert format_activity_line(raw).startswith("Tool returned [Control Device]: verification=")
    assert "Malformed" not in format_activity_line(raw)
