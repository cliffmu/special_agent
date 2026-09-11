"""Tool activity reports outcomes and timing without exposing arguments/results."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from special_agent.utils import logging as activity_log
from special_agent.utils import performance
from special_agent.utils.response_utils import validate_and_execute_tools


@pytest.mark.parametrize("result,status", [
    ({"error": "PRIVATE_RESULT"}, "error"),
    ({"success": False, "message": "PRIVATE_RESULT"}, "error"),
    ({"status": "failed", "message": "PRIVATE_RESULT"}, "error"),
    ({"status": "partial", "message": "PRIVATE_RESULT"}, "partial"),
    ({"status": "ok", "message": "PRIVATE_RESULT"}, "ok"),
    ("Error: PRIVATE_RESULT", "error"),
])
async def test_explicit_tool_outcomes_are_logged_without_changing_results(monkeypatch, caplog, result, status):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(performance, "_enabled", False)
    spec = SimpleNamespace(name="registered_tool", func=AsyncMock(return_value=result),
                           validate=None, can_run_parallel=True)
    call = SimpleNamespace(name=spec.name, arguments='{"query":"PRIVATE_ARGUMENT"}', call_id="private-call-id")
    token = activity_log.begin_request()
    try:
        output = await validate_and_execute_tools([call], {spec.name: spec}, set(), None)
    finally:
        activity_log.end_request(token)
    lines = [record.getMessage() for record in caplog.records
             if record.name == "custom_components.special_agent.activity"]
    assert len(lines) == 2 and "phase=started" in lines[0]
    assert "phase=finished" in lines[1] and "status=" + status in lines[1]
    fields = [dict(field.split("=", 1) for field in line.split()) for line in lines]
    assert fields[0]["request"] == fields[1]["request"] != "-"
    assert int(fields[1]["elapsed_ms"]) >= 0
    assert "PRIVATE" not in caplog.text and "private-call-id" not in caplog.text
    assert not output.all_failed, "activity classification must not change existing result handling"
    assert "PRIVATE_RESULT" in output.messages[0]["output"]


async def test_tool_exception_is_logged_without_its_private_message(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(performance, "_enabled", False)
    spec = SimpleNamespace(name="registered_tool", func=AsyncMock(side_effect=ValueError("PRIVATE_EXCEPTION")),
                           validate=None, can_run_parallel=True)
    call = SimpleNamespace(name=spec.name, arguments='{"query":"PRIVATE_ARGUMENT"}', call_id="private-call-id")
    output = await validate_and_execute_tools([call], {spec.name: spec}, set(), None)
    assert output.all_failed and "PRIVATE_EXCEPTION" in output.error_message
    assert "status=error" in caplog.text and "error_type=ValueError" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize('malformed', ['{"query":', '[1,2]', 'null'])
async def test_invalid_json_does_not_abort_other_valid_tool_calls(monkeypatch, caplog, malformed):
    monkeypatch.setattr(performance, "_enabled", False)
    spec = SimpleNamespace(name="registered_tool", func=AsyncMock(return_value={"status": "ok"}),
                           validate=None, can_run_parallel=True)
    calls = [SimpleNamespace(name=spec.name, arguments=malformed, call_id="bad"),
             SimpleNamespace(name=spec.name, arguments='{"query":"valid"}', call_id="good")]
    output = await validate_and_execute_tools(calls, {spec.name: spec}, set(), None)
    spec.func.assert_awaited_once_with(query="valid")
    by_id = {item['call_id']: item['output'] for item in output.messages}
    assert 'arguments were invalid' in by_id['bad']
    assert 'ok' in by_id['good']
    assert 'status=invalid_arguments' in caplog.text


def test_long_scene_and_final_failure_remain_complete_json():
    import json
    from special_agent.utils.response_utils import summarize_result
    steps = [{"service": "light.turn_on", "data": {"entity_id": f"light.test_{i}"},
              "status": "ok", "verification": "verified", "padding": "x" * 150} for i in range(16)]
    routine = {"scenes": [{"id": "test_scene", "commands_list": steps}]}
    assert len(json.dumps(routine)) > 2000
    assert json.loads(summarize_result(routine)) == routine
    steps[-1].update(status="error", verification="failed")
    result = {"steps": steps, "result": "failed", "verification": "failed", "completed_steps": 15}
    assert json.loads(summarize_result(result)) == result
    assert json.loads(summarize_result(steps)) == steps


def test_oversized_observation_keeps_outcome_and_never_supplies_partial_commands():
    import json
    from special_agent.utils.response_utils import summarize_result
    result = {"steps": [{"payload": "x" * 65000}], "result": "failed", "verification": "failed",
              "accepted": True, "completed_steps": 15}
    observation = json.loads(summarize_result(result))
    assert observation["observation_status"] == "details_omitted"
    assert observation["reported_outcome"]["verification"] == "failed"
    assert observation["reported_outcome"]["accepted"] is True
    assert "steps" not in observation
    assert "Do not repeat a modifying action" in observation["message"]
