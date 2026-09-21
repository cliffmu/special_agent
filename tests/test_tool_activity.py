"""Tool activity reports outcomes and timing without exposing arguments/results."""

import asyncio
import json
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
    lines = [getattr(record, "special_agent_activity", record.getMessage()) for record in caplog.records
             if record.name == "custom_components.special_agent.activity"
             and getattr(record, "special_agent_activity", record.getMessage()).startswith("event=tool ")]
    assert len(lines) == 2 and "phase=started" in lines[0]
    assert "phase=finished" in lines[1] and "status=" + status in lines[1]
    fields = [dict(field.split("=", 1) for field in line.split()) for line in lines]
    assert fields[0]["request"] == fields[1]["request"] != "-"
    assert int(fields[1]["elapsed_ms"]) >= 0
    assert "PRIVATE" not in caplog.text and "private-call-id" not in caplog.text
    assert not output.all_failed, "activity classification must not change existing result handling"
    assert "PRIVATE_RESULT" in output.messages[0]["output"]
    batch_line = next(getattr(record, "special_agent_activity", record.getMessage()) for record in caplog.records
                      if getattr(record, "special_agent_activity", record.getMessage()).startswith("event=tool_batch ")
                      and "phase=finished" in getattr(record, "special_agent_activity", record.getMessage()))
    assert "status=" + ("completed" if status == "ok" else status) in batch_line


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


async def test_parallel_batch_traces_actual_validated_inputs_and_model_outputs(monkeypatch, caplog):
    monkeypatch.setattr(performance, "_enabled", False)
    monkeypatch.setattr(activity_log, "_TRACE_ENABLED", True)
    entered = [asyncio.Event(), asyncio.Event()]

    async def lookup(index, query, password):
        entered[index].set()
        await asyncio.wait_for(entered[1 - index].wait(), timeout=1)
        return {"index": index, "query": query, "token": "PRIVATE_RESULT_TOKEN"}

    spec = SimpleNamespace(name="lookup", func=lookup, can_run_parallel=True,
                           validate=lambda args: {**args, "query": args["query"].strip()})
    calls = [SimpleNamespace(name="lookup", call_id=f"call_{index}", arguments=json.dumps({
        "index": index, "query": f" room {index} ", "password": "PRIVATE_PASSWORD",
    })) for index in range(2)]
    result = await validate_and_execute_tools(calls, {"lookup": spec}, set(), None, iteration=2)

    summaries = [getattr(record, "special_agent_activity", record.getMessage()) for record in caplog.records
                 if record.name == "custom_components.special_agent.activity"]
    batches = [dict(field.split("=", 1) for field in line.split()) for line in summaries
               if line.startswith("event=tool_batch ")]
    assert [batch["phase"] for batch in batches] == ["planned", "started", "progress", "progress", "finished"]
    assert batches[1]["mode"] == "parallel" and batches[1]["tool_count"] == "2"
    assert len({batch["batch"] for batch in batches}) == 1
    tool_events = [dict(field.split("=", 1) for field in line.split()) for line in summaries
                   if line.startswith("event=tool ")]
    assert {item["batch"] for item in tool_events} == {batches[0]["batch"]}
    assert {item["iteration"] for item in tool_events} == {"2"}
    assert len({item["call_id"] for item in tool_events}) == 2
    details = [getattr(record, "special_agent_activity", record.getMessage()) for record in caplog.records
               if record.name == "custom_components.special_agent.trace"]
    inputs = [json.loads(line.split(" payload=", 1)[1]) for line in details
              if line.startswith("event=tool_input ")]
    outputs = [json.loads(line.split(" payload=", 1)[1]) for line in details
               if line.startswith("event=tool_output ")]
    assert {item["query"] for item in inputs} == {"room 0", "room 1"}
    assert {item["index"] for item in outputs} == {0, 1}
    assert all(item["password"] == "[redacted]" for item in inputs)
    assert all(item["token"] == "[redacted]" for item in outputs)
    assert "PRIVATE" not in caplog.text
    assert all("PRIVATE_RESULT_TOKEN" in item["output"] for item in result.messages)


async def test_skipped_tools_keep_call_correlation_and_explain_serial_requirement(monkeypatch, caplog):
    monkeypatch.setattr(performance, "_enabled", False)
    monkeypatch.setattr(activity_log, "_TRACE_ENABLED", True)
    lookup = SimpleNamespace(name="lookup", func=AsyncMock(return_value={"ok": True}),
                             validate=None, can_run_parallel=True)
    confirm = SimpleNamespace(name="confirm", func=AsyncMock(), validate=None, can_run_parallel=False)
    calls = [SimpleNamespace(name="lookup", arguments='{"query":"duplicate"}', call_id="dup"),
             SimpleNamespace(name="lookup", arguments='{"query":"new"}', call_id="valid"),
             SimpleNamespace(name="confirm", arguments='{"question":"Proceed?"}', call_id="serial")]
    tried = {("lookup", json.dumps({"query": "duplicate"}, sort_keys=True))}
    result = await validate_and_execute_tools(calls, {"lookup": lookup, "confirm": confirm}, tried, None, iteration=3)

    lookup.func.assert_awaited_once_with(query="new")
    confirm.func.assert_not_awaited()
    assert len(result.messages) == 3
    summaries = [getattr(record, "special_agent_activity", record.getMessage()) for record in caplog.records
                 if record.name == "custom_components.special_agent.activity"]
    skipped = [dict(field.split("=", 1) for field in line.split()) for line in summaries
               if "phase=skipped" in line]
    assert {item["status"] for item in skipped} == {"not_parallel", "duplicate"}
    assert {item["call_id"] for item in skipped} == {
        activity_log.device_correlation("dup"), activity_log.device_correlation("serial"),
    }
    assert any("mode=single" in line and "function_count=3" in line and "tool_count=1" in line
               for line in summaries)
    assert "event=tool_skipped" in caplog.text and "cannot run in parallel" in caplog.text


async def test_cancelled_tool_finishes_its_correlated_activity(monkeypatch, caplog):
    monkeypatch.setattr(performance, "_enabled", False)
    spec = SimpleNamespace(name="lookup", func=AsyncMock(side_effect=asyncio.CancelledError()),
                           validate=None, can_run_parallel=True)
    call = SimpleNamespace(name="lookup", arguments="{}", call_id="cancelled")
    with pytest.raises(asyncio.CancelledError):
        await validate_and_execute_tools([call], {"lookup": spec}, set(), None, iteration=4)
    lines = [getattr(record, "special_agent_activity", record.getMessage()) for record in caplog.records
             if record.name == "custom_components.special_agent.activity"
             and getattr(record, "special_agent_activity", record.getMessage()).startswith("event=tool ")]
    assert len(lines) == 2
    assert "phase=finished" in lines[1] and "status=cancelled" in lines[1]
    assert "call_id=" + activity_log.device_correlation("cancelled") in lines[1]


async def test_fast_tool_is_visible_while_model_observations_wait_for_whole_batch(monkeypatch, caplog):
    monkeypatch.setattr(activity_log, "_TRACE_ENABLED", True)
    monkeypatch.setattr(performance, "_enabled", False)
    fast_returned, slow_started, release_slow = (asyncio.Event() for _ in range(3))

    async def lookup(which):
        if which == "slow":
            slow_started.set()
            await release_slow.wait()
        else:
            await slow_started.wait()
            fast_returned.set()
        return {"which": which, "state": "on"}

    spec = SimpleNamespace(name="lookup", func=lookup, validate=None, can_run_parallel=True)
    calls = [SimpleNamespace(name="lookup", call_id=which, arguments=json.dumps({"which": which}))
             for which in ("fast", "slow")]
    batch = asyncio.create_task(validate_and_execute_tools(calls, {"lookup": spec}, set(), None, iteration=2))
    try:
        await asyncio.wait_for(fast_returned.wait(), 1)
        assert not batch.done()
        raw = [getattr(record, "special_agent_activity", "") for record in caplog.records]
        assert any(line.startswith("event=tool_result ") and '"which":"fast"' in line for line in raw)
        waiting = next(line for line in raw if "phase=progress" in line)
        assert "completed_steps=1" in waiting and "remaining=1" in waiting
        assert "decision=wait_for_all_tools" in waiting
        assert not any(line.startswith("event=tool_output ") for line in raw)
    finally:
        release_slow.set()
        result = await asyncio.wait_for(batch, 1)
    assert {json.loads(item["output"])["which"] for item in result.messages} == {"fast", "slow"}
    assert any("remaining=0" in record.getMessage() for record in caplog.records)
