import asyncio

import pytest

from special_agent.agent_core import Agent
from special_agent.tool_specs.run_sequence import run_sequence
from special_agent.utils.scene_memory_store import normalize_and_validate_steps
from special_agent.utils.tool_registry import (
    ToolSpec,
    get_registered_tool_specs,
    restore_tool_registry,
)


@pytest.fixture
def dummy_tool_registration():
    saved_registry = get_registered_tool_specs()

    async def _dummy_tool(value: str, hass=None):
        return {"echo": value, "uri": value}

    spec = ToolSpec(
        name="dummy_tool",
        description="Dummy tool for tests",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        returns="dict",
        func=_dummy_tool,
        can_run_in_sequence=True,
    )

    agent = Agent()
    agent.register_tool(spec)

    try:
        yield spec
    finally:
        restore_tool_registry(saved_registry)


def test_run_sequence_tool_call_success(dummy_tool_registration, hass):
    sequence = {
        "steps": [
            {
                "type": "tool_call",
                "tool": "dummy_tool",
                "args": {"value": "hello"},
                "result_var": "song_uri",
                "result_path": "uri",
                "expect": {"path": "uri", "equals": "hello"},
            },
            {
                "type": "tool_call",
                "tool": "dummy_tool",
                "args": {"value": "${song_uri}"},
                "expect": {"equals": {"echo": "hello", "uri": "hello"}},
            },
        ]
    }

    result = asyncio.run(run_sequence(sequence=sequence, hass=hass))

    assert result["result"] == "completed"
    first_step, second_step = result["steps"]
    assert first_step["status"] == "ok"
    assert first_step["stored_var"] == "song_uri"
    assert first_step["stored_value"] == "hello"
    assert first_step["expect_verified"] is True

    assert second_step["status"] == "ok"
    assert second_step["tool_result"] == {"echo": "hello", "uri": "hello"}


def test_run_sequence_tool_expectation_failure(dummy_tool_registration, hass):
    sequence = {
        "steps": [
            {
                "type": "tool_call",
                "tool": "dummy_tool",
                "args": {"value": "mismatch"},
                "expect": {"equals": "expected"},
            },
            {
                "type": "delay",
                "seconds": 1,
            },
        ]
    }

    result = asyncio.run(run_sequence(sequence=sequence, hass=hass))

    assert result["result"] == "failed"
    assert "tool expectation failure" in result["error"]
    step = result["steps"][0]
    assert step["status"] == "error"
    assert step["expect_failed"] is True
    assert "Expected expected" in step["error"]
    assert len(result["steps"]) == 1  # second step skipped due to abort


def test_normalize_accepts_tool_call(dummy_tool_registration):
    raw_steps = [
        {
            "type": "tool_call",
            "tool": "dummy_tool",
            "args": {"value": "hello"},
            "result_var": "uri",
            "result_path": "uri",
            "expect": {"path": "uri", "exists": True},
        }
    ]

    normalized, errors = normalize_and_validate_steps(raw_steps)

    assert errors == []
    assert normalized[0]["tool"] == "dummy_tool"
    assert normalized[0]["result_var"] == "uri"
    assert normalized[0]["result_path"] == "uri"


def test_normalize_rejects_unknown_tool(dummy_tool_registration):
    raw_steps = [
        {
            "type": "tool_call",
            "tool": "not_registered",
            "args": {},
        }
    ]

    normalized, errors = normalize_and_validate_steps(raw_steps)

    assert normalized == []
    assert any("not allowed" in err for err in errors)
