"""Exercise the authenticated adapter with HA's external framework types stubbed."""

import asyncio
import importlib.util
import json
import sys
import types
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from special_agent import agent_core
from special_agent.utils import logging as activity_log


@pytest.fixture
def bridge(monkeypatch):
    components = types.ModuleType("homeassistant.components")
    conversation = types.ModuleType("homeassistant.components.conversation")
    conversation.AbstractConversationAgent = type("AbstractConversationAgent", (), {})
    conversation.ConversationEntity = type("ConversationEntity", (), {})

    class Result:
        def __init__(self, conversation_id, response):
            self.conversation_id, self.response = conversation_id, response

        def as_dict(self):
            return {"conversation_id": self.conversation_id, "response": {"speech": {"plain": {"speech": "Done"}}}}

    conversation.ConversationResult = Result
    http = types.ModuleType("homeassistant.components.http")
    http.KEY_HASS = "hass"
    http.HomeAssistantView = type("HomeAssistantView", (), {
        "context": lambda self, request: request.context,
        "json": staticmethod(web.json_response),
    })
    components.conversation, components.http = conversation, http
    for module in (components, conversation, http):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    def load(name, filename):
        spec = importlib.util.spec_from_file_location(name, Path(agent_core.__file__).with_name(filename))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    adapter = load("special_agent.conversation", "conversation.py")
    api = load("special_agent._live_api_test", "live_api.py")
    monkeypatch.setattr(adapter, "intent", SimpleNamespace(IntentResponse=Mock()))
    monkeypatch.setattr(agent_core.performance, "_enabled", False)
    entry = SimpleNamespace(entry_id="entry", data={"agent_model": "gpt-6-astra", "reasoning_effort": "max", "fast_mode": True}, options={})
    entity = adapter.SpecialAgentConversation(entry)
    entity.hass = SimpleNamespace(data={"special_agent": {
        "conversation_agents": {"entry": entity}, "sessions": SimpleNamespace(save=AsyncMock()),
    }}, http=SimpleNamespace(register_view=Mock()), services=SimpleNamespace(has_service=Mock(return_value=True)))
    conversation.async_get_agent = Mock(return_value=entity)

    async def converse(hass, **data):
        return await entity.async_process(SimpleNamespace(**data))

    conversation.async_converse = AsyncMock(side_effect=converse)
    monkeypatch.setattr(agent_core, "load_all_tools", AsyncMock(return_value={"tool": object()}))
    execute = AsyncMock(return_value="Done")
    monkeypatch.setattr(agent_core, "plan_execute", execute)
    monkeypatch.setattr(activity_log, "_ACTIVITY_BUFFER", activity_log.ActivityBuffer())
    return SimpleNamespace(api=api, adapter=adapter, conversation=conversation, entity=entity,
                           hass=entity.hass, execute=execute)


def request(bridge, data=None, *, raw=None, query=None, content_length=None):
    body = raw if raw is not None else json.dumps(data).encode()

    async def chunks(size):
        for position in range(0, len(body), size):
            yield body[position:position + size]

    return SimpleNamespace(app={"hass": bridge.hass}, context=object(), query=query or {},
                           content_length=content_length, content=SimpleNamespace(iter_chunked=chunks))


async def post(bridge, **data):
    return await bridge.api.LiveProcessView().post(request(bridge, {"agent_id": "conversation.special_agent", "text": "Hello", **data}))


async def test_new_sessions_are_unique_and_returned_id_is_reused(bridge):
    first = json.loads((await post(bridge)).body)
    second = json.loads((await post(bridge)).body)
    continued = json.loads((await post(bridge, conversation_id=first["conversation_id"])).body)
    assert uuid.UUID(first["conversation_id"]) != uuid.UUID(second["conversation_id"])
    assert continued == first
    assert first["response"]["speech"]["plain"]["speech"] == "Done"
    assert [call.kwargs["session_key"] for call in bridge.execute.await_args_list] == [
        (first["conversation_id"], ""), (second["conversation_id"], ""), (first["conversation_id"], "")]
    assert all(call.kwargs["device_id"] is None for call in bridge.conversation.async_converse.await_args_list)


async def test_live_settings_are_scoped_without_reloading_tools_or_mutating_saved_config(bridge):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def execute(*args, **settings):
        calls.append(settings)
        if settings["model"] == "gpt-5.6-terra":
            entered.set()
            await release.wait()
        return "Done"

    bridge.execute.side_effect = execute
    cached_agent = bridge.entity.agent
    live = asyncio.create_task(post(bridge, model="gpt-5.6-terra", reasoning_effort="low", fast_mode=False))
    await entered.wait()
    await bridge.entity.async_process(SimpleNamespace(text="Ordinary request", conversation_id="normal", device_id=None, language="en"))
    release.set()
    await live
    await post(bridge, model="gpt-5.6-terra", reasoning_effort="low", fast_mode=False)
    assert [(item["model"], item["reasoning_effort"], item["fast_mode"]) for item in calls] == [
        ("gpt-5.6-terra", "low", False), ("gpt-6-astra", "max", True), ("gpt-5.6-terra", "low", False)]
    assert bridge.entity.agent is cached_agent
    agent_core.load_all_tools.assert_awaited_once()
    assert cached_agent.config == bridge.entity.config_entry.data
    assert bridge.adapter._LIVE_MODEL_SETTINGS.get() is None


@pytest.mark.parametrize("failure", [RuntimeError("PRIVATE"), asyncio.CancelledError()])
async def test_request_scope_resets_on_error_or_cancellation(bridge, failure):
    bridge.conversation.async_converse.side_effect = failure
    with pytest.raises(type(failure)):
        await post(bridge, model="gpt-5.6-luna", fast_mode=False)
    assert bridge.adapter._LIVE_MODEL_SETTINGS.get() is None


@pytest.mark.parametrize("changes", [
    {"text": ""}, {"text": " "}, {"text": "x" * 65537}, {"text": 1},
    {"agent_id": ""}, {"agent_id": "agent\nPRIVATE"}, {"conversation_id": None},
    {"conversation_id": "x" * 129}, {"language": "en\n"}, {"model": "other"},
    {"reasoning_effort": "ultra"}, {"fast_mode": "false"}, {"fast_mode": 0},
    {"api_key": "PRIVATE"}, {"device_id": "satellite"},
])
async def test_invalid_request_is_rejected_before_dispatch(bridge, changes):
    with pytest.raises(web.HTTPBadRequest):
        await post(bridge, **changes)
    bridge.conversation.async_converse.assert_not_awaited()


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b"\xff", b"null", b"[" * 2000 + b"]" * 2000])
async def test_invalid_json_is_rejected(bridge, raw):
    with pytest.raises(web.HTTPBadRequest):
        await bridge.api.LiveProcessView().post(request(bridge, raw=raw))


@pytest.mark.parametrize("content_length", [None, 1024 * 1024 + 1])
async def test_body_size_is_bounded_even_without_content_length(bridge, content_length):
    with pytest.raises(web.HTTPRequestEntityTooLarge):
        await bridge.api.LiveProcessView().post(request(bridge, raw=b" " * (1024 * 1024 + 1), content_length=content_length))
    bridge.conversation.async_converse.assert_not_awaited()


@pytest.mark.parametrize("kind", ["unknown", "other_agent", "unloaded"])
async def test_only_current_registered_special_agent_can_be_called(bridge, kind):
    if kind == "unloaded":
        bridge.hass.data["special_agent"]["conversation_agents"].clear()
    else:
        bridge.conversation.async_get_agent.return_value = None if kind == "unknown" else object()
    with pytest.raises(web.HTTPBadRequest):
        await post(bridge)
    bridge.conversation.async_converse.assert_not_awaited()


def test_views_are_authenticated_and_registered_once(bridge):
    bridge.api.register_views(bridge.hass)
    bridge.api.register_views(bridge.hass)
    views = [call.args[0] for call in bridge.hass.http.register_view.call_args_list]
    assert len(views) == 2
    assert all(view.requires_auth is True for view in views)
    assert {view.url for view in views} == {"/api/special_agent/live/process", "/api/special_agent/live/activity"}


async def test_platform_setup_records_the_same_agent_registered_with_ha(bridge):
    bridge.conversation.async_set_agent = Mock()
    add_entities = Mock()
    await bridge.adapter.async_setup_entry(bridge.hass, bridge.entity.config_entry, add_entities)
    agent = add_entities.call_args.args[0][0]
    assert bridge.hass.data["special_agent"]["conversation_agents"]["entry"] is agent
    assert bridge.conversation.async_set_agent.call_args.args[2] is agent


@pytest.mark.parametrize("unloaded", [True, False])
async def test_successful_unload_removes_only_the_inactive_agent(bridge, unloaded):
    from special_agent import async_unload_entry

    bridge.hass.config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=unloaded))
    bridge.hass.data["special_agent"]["conversation_agents"]["other"] = object()
    assert await async_unload_entry(bridge.hass, bridge.entity.config_entry) is unloaded
    active = bridge.hass.data["special_agent"]["conversation_agents"]
    assert ("entry" in active) is not unloaded
    assert "other" in active


async def test_activity_endpoint_returns_only_sanitized_activity(bridge):
    activity_log.info("PRIVATE_CORE_MESSAGE")
    activity_log.activity("llm_request", model="gpt-5.6-terra", prompt="PRIVATE_PROMPT", api_key="PRIVATE_KEY")
    response = await bridge.api.LiveActivityView().get(request(bridge, query={"cursor": "0", "limit": "1"}))
    result = json.loads(response.body)
    assert result["cursor"] == 1 and result["has_more"] is False
    assert "model=gpt-5.6-terra" in result["records"][0]["line"]
    assert "PRIVATE" not in response.text


@pytest.mark.parametrize("query", [{"cursor": "-1"}, {"cursor": "bad"}, {"cursor": str(2**63)},
                                     {"limit": "201"}, {"limit": "0"}, {"epoch": "bad\nepoch"}, {"source": "all"}])
async def test_activity_query_is_bounded(bridge, query):
    with pytest.raises(web.HTTPBadRequest):
        await bridge.api.LiveActivityView().get(request(bridge, query=query))
