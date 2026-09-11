"""Request isolation and bounded recovery against the current Responses API loop."""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from special_agent import agent_core
from special_agent.utils.llm_client import handle_llm_error


@pytest.fixture
def conversation_entity(monkeypatch):
    """Load the HA adapter with only its external entity/response types stubbed."""
    conversation_types = types.ModuleType("homeassistant.components.conversation")
    conversation_types.AbstractConversationAgent = type("AbstractConversationAgent", (), {})
    conversation_types.ConversationEntity = type("ConversationEntity", (), {})
    conversation_types.ConversationResult = SimpleNamespace
    monkeypatch.setitem(sys.modules, conversation_types.__name__, conversation_types)
    spec = importlib.util.spec_from_file_location(
        "special_agent._conversation_lifecycle_test",
        Path(agent_core.__file__).with_name("conversation.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.intent = SimpleNamespace(IntentResponse=Mock())
    monkeypatch.setattr(agent_core.performance, "_enabled", False)
    entry = SimpleNamespace(data={"require_confirmation": True}, options={})
    entity = module.SpecialAgentConversation(entry)
    entity.hass = SimpleNamespace(
        data={"special_agent": {"sessions": SimpleNamespace(save=AsyncMock())}},
        services=SimpleNamespace(has_service=Mock(return_value=True), async_call=AsyncMock()),
    )
    return entity


def user_input(device_id="satellite"):
    return SimpleNamespace(
        text="Hello", conversation_id="conversation", device_id=device_id, language="en"
    )


@pytest.mark.asyncio
async def test_unchanged_requests_reuse_loaded_tools(monkeypatch, conversation_entity):
    load_tools = AsyncMock(return_value={"tool": object()})
    monkeypatch.setattr(agent_core, "load_all_tools", load_tools)
    monkeypatch.setattr(agent_core, "plan_execute", AsyncMock(return_value="Hello"))
    agent = conversation_entity.agent

    await conversation_entity.async_process(user_input())
    await conversation_entity.async_process(user_input())

    assert conversation_entity.agent is agent
    load_tools.assert_awaited_once()
    assert list(agent.tools) == ["tool"]


@pytest.mark.asyncio
async def test_simultaneous_requests_share_initial_tool_load(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def load_tools(hass, config):
        started.set()
        await release.wait()
        return {"tool": object()}

    loader = AsyncMock(side_effect=load_tools)
    execute = AsyncMock(return_value="done")
    monkeypatch.setattr(agent_core, "load_all_tools", loader)
    monkeypatch.setattr(agent_core, "plan_execute", execute)
    agent = agent_core.Agent()
    first = asyncio.create_task(agent.plan("first"))
    await started.wait()
    second = asyncio.create_task(agent.plan("second"))
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(first, second) == ["done", "done"]
    loader.assert_awaited_once()
    assert execute.await_args_list[0].args[1] == execute.await_args_list[1].args[1]


@pytest.mark.asyncio
async def test_options_change_does_not_mutate_running_request(monkeypatch, conversation_entity):
    started, release = asyncio.Event(), asyncio.Event()
    old_tool, new_tool = object(), object()

    async def load_tools(hass, config):
        if config["require_confirmation"]:
            started.set()
            await release.wait()
            return {"confirm_action": old_tool}
        return {"control_device": new_tool}

    execute = AsyncMock(return_value="done")
    monkeypatch.setattr(agent_core, "load_all_tools", load_tools)
    monkeypatch.setattr(agent_core, "plan_execute", execute)
    old_agent = conversation_entity.agent
    first = asyncio.create_task(conversation_entity.async_process(user_input()))
    await started.wait()
    conversation_entity.config_entry.options = {"require_confirmation": False}
    await conversation_entity.async_process(user_input("other_satellite"))
    release.set()
    await first

    assert conversation_entity.agent is not old_agent
    assert old_agent.config["require_confirmation"] is True
    assert execute.await_args_list[0].kwargs["require_confirmation"] is False
    assert execute.await_args_list[0].args[1] == [new_tool]
    assert execute.await_args_list[1].kwargs["require_confirmation"] is True
    assert execute.await_args_list[1].args[1] == [old_tool]


@pytest.mark.asyncio
@pytest.mark.parametrize("device_id", ["", "satellite"])
async def test_only_satellite_requests_restart_tts_pipeline(
    monkeypatch, conversation_entity, device_id
):
    monkeypatch.setattr(agent_core, "load_all_tools", AsyncMock(return_value={}))
    monkeypatch.setattr(
        agent_core, "plan_execute",
        AsyncMock(return_value={"prompt_payload": {"speak": "Hello"}}),
    )

    result = await conversation_entity.async_process(user_input(device_id))

    assert result.response is not None
    assert conversation_entity.hass.services.async_call.await_count == bool(device_id)


@pytest.fixture
def prepared_loop(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(agent_core.performance, "_enabled", False)
    monkeypatch.setattr(agent_core, "get_async_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(agent_core, "build_system_prompt", AsyncMock(return_value="system"))
    history = [{"role": "system", "content": "system"}, {"role": "user", "content": "hi"}]
    monkeypatch.setattr(agent_core, "load_session", Mock(return_value=(history, None, None, None)))
    save = Mock()
    monkeypatch.setattr(agent_core, "store_session", save)
    return history, save


@pytest.mark.asyncio
async def test_recoverable_api_errors_have_finite_retry_budget(monkeypatch, prepared_loop):
    history, save = prepared_loop
    call = AsyncMock(side_effect=RuntimeError("recoverable"))
    monkeypatch.setattr(agent_core, "call_llm", call)
    monkeypatch.setattr(agent_core, "handle_llm_error", lambda error, messages: (None, messages))

    result = await asyncio.wait_for(agent_core.plan_execute("hi", []), timeout=1)

    assert call.await_count == 3  # Initial attempt plus two recoveries.
    assert "start a new request" in result
    save.assert_called_once()


@pytest.mark.parametrize("error_code", ["context_overflow", "context_length_exceeded"])
def test_context_recovery_preserves_complete_current_tool_exchange(error_code):
    old = [{"role": "system", "content": "system"}, {"role": "user", "content": "old"}]
    # The ten-message cutoff lands inside the current tool exchange.
    current = [{"role": "user", "content": "new"}]
    for i in range(6):
        current.extend([
            SimpleNamespace(type="function_call", call_id=str(i)),
            {"type": "function_call_output", "call_id": str(i), "output": "ok"},
        ])

    error, trimmed = handle_llm_error(RuntimeError(error_code), old + current)

    assert error is None
    assert trimmed == old[:1] + current


def test_context_overflow_with_no_older_turn_stops_without_duplicating_messages():
    history = [{"role": "system", "content": "system"}, {"role": "user", "content": "large"}]

    error, unchanged = handle_llm_error(RuntimeError("context_overflow"), history)

    assert "shorter request" in error
    assert unchanged == history
