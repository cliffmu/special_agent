import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from special_agent.agent_core import Agent, plan_execute
from special_agent.session_store import SessionManager
from special_agent import DOMAIN


class FakeClient:
    def __init__(self, msg):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._msg = msg

    async def _create(self, *args, **kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=self._msg)])


@pytest.fixture
def fake_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "1")

    def factory(msg):
        async def get_client(hass=None):
            return FakeClient(msg)
        monkeypatch.setattr(
            "special_agent.utils.openai_client.get_async_client", get_client
        )
    return factory


class SeqClient:
    """Fake client that yields a sequence of messages."""

    def __init__(self, msgs):
        self._msgs = list(msgs)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *args, **kw):
        msg = self._msgs.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _control_msg(brightness):
    call = SimpleNamespace(
        id="1",
        function=SimpleNamespace(
            name="control_device",
            arguments=json.dumps(
                {
                    "service": "light.turn_on",
                    "data": {"entity_id": ["light.kitchen"], "brightness_pct": brightness},
                }
            ),
        ),
    )

    class ToolList(list):
        @property
        def function(self):
            return self[0].function

    tool_list = ToolList([call])
    return SimpleNamespace(
        content=None,
        tool_calls=tool_list,
        model_dump=lambda: {"content": None, "tool_calls": tool_list},
        dict=lambda: {"content": None, "tool_calls": tool_list},
    )


def _text_msg(text="done"):
    return SimpleNamespace(
        content=text,
        tool_calls=None,
        model_dump=lambda: {"content": text, "tool_calls": None},
        dict=lambda: {"content": text, "tool_calls": None},
    )


def make_msg():
    call = SimpleNamespace(
        id="1",
        function=SimpleNamespace(
            name="confirm_action",
            arguments=json.dumps({
                "action": "turn on",
                "targets": ["light.kitchen"],
                "question": "Turn on kitchen?",
            }),
        ),
    )
    class ToolList(list):
        @property
        def function(self):
            return self[0].function

    tool_list = ToolList([call])
    msg = SimpleNamespace(
        content=None,
        tool_calls=tool_list,
        model_dump=lambda: {"content": None, "tool_calls": tool_list},
        dict=lambda: {"content": None, "tool_calls": tool_list},
    )
    return msg


def test_multi_session(fake_openai):
    msg = make_msg()
    fake_openai(msg)
    hass = MagicMock()
    mgr = SessionManager(hass)
    hass.data = {DOMAIN: {"sessions": mgr}}

    agent = Agent()

    res1 = asyncio.run(plan_execute("hi", list(agent.tools.values()), hass=hass, session_key=("a", "dev1")))
    res2 = asyncio.run(plan_execute("hi", list(agent.tools.values()), hass=hass, session_key=("b", "dev2")))

    assert isinstance(res1, dict) and "prompt_payload" in res1
    assert mgr.get("a|dev1") is not None
    assert mgr.get("b|dev2") is not None


def test_percent_followup(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "1")

    hass = MagicMock()
    hass.services.async_call = MagicMock()
    mgr = SessionManager(hass)
    hass.data = {DOMAIN: {"sessions": mgr}}

    agent = Agent()

    async def stub_control(service: str, data: dict | None = None, hass=None):
        hass.services.async_call(service.split(".")[0], service.split(".")[1], data or {}, blocking=True)
        return {"status": "OK", "focus": {"targets": data.get("entity_id"), "action": service}}

    from special_agent.tool_specs import control_device as cd

    monkeypatch.setattr(cd, "control_device", stub_control)
    monkeypatch.setattr(cd.SPEC, "func", stub_control)

    # first command -> brightness 5
    msgs1 = [_control_msg(5), _text_msg()]
    async def get_client1(hass=None):
        return SeqClient(msgs1)

    monkeypatch.setattr(
        "special_agent.utils.openai_client.get_async_client",
        get_client1,
    )
    res1 = asyncio.run(
        plan_execute("office lights 5%", list(agent.tools.values()), hass=hass, session_key=("a", "dev"))
    )
    assert res1 == "done"
    assert mgr.get("a|dev").focus["targets"] == ["light.kitchen"]

    # follow-up with percent only
    msgs2 = [_text_msg("yes"), _control_msg(6), _text_msg("done2")]
    async def get_client2(hass=None):
        return SeqClient(msgs2)

    monkeypatch.setattr(
        "special_agent.utils.openai_client.get_async_client",
        get_client2,
    )
    res2 = asyncio.run(
        plan_execute("6%", list(agent.tools.values()), hass=hass, session_key=("a", "dev"))
    )
    assert res2 == "done2"
    hass.services.async_call.assert_any_call(
        "light", "turn_on", {"entity_id": ["light.kitchen"], "brightness_pct": 6}, blocking=True
    )
