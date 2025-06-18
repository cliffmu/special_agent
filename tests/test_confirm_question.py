import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from special_agent.agent_core import Agent, plan_execute
from special_agent.session_store import SessionManager
from special_agent import DOMAIN


class FakeClient:
    def __init__(self, msg):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._msg = msg

    async def _create(self, *args, **kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=self._msg)])


def make_msg():
    call = SimpleNamespace(
        id="1",
        function=SimpleNamespace(
            name="confirm_action",
            arguments=json.dumps({
                "action": "turn off",
                "targets": ["light.kitchen"],
                "question": "Turn off kitchen?",
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


def test_confirm_question(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "1")
    msg = make_msg()

    async def get_client(hass=None):
        return FakeClient(msg)

    monkeypatch.setattr("special_agent.utils.openai_client.get_async_client", get_client)

    hass = MagicMock()
    mgr = SessionManager(hass)
    hass.data = {DOMAIN: {"sessions": mgr}}

    agent = Agent()

    result = asyncio.run(plan_execute("hi", list(agent.tools.values()), hass=hass, session_key=("c", "dev")))

    assert isinstance(result, dict) and "prompt_payload" in result
    assert mgr.get("c|dev") is not None
