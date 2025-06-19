import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from special_agent.agent_core import Agent, plan_execute
from special_agent.session_store import SessionManager
from special_agent import DOMAIN


class SeqClient:
    def __init__(self, msgs):
        self._msgs = list(msgs)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *args, **kw):
        msg = self._msgs.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _dup_msg():
    call = SimpleNamespace(
        id="1",
        function=SimpleNamespace(
            name="get_entity_state",
            arguments=json.dumps({"entity_ids": ["light.kitchen"], "attributes": ["brightness"]}),
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


@pytest.mark.asyncio
async def test_duplicate_depth_limit(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "1")
    msgs = [_dup_msg() for _ in range(10)]
    client = SeqClient(msgs)

    async def get_client(hass=None):
        return client

    monkeypatch.setattr("special_agent.utils.openai_client.get_async_client", get_client)

    hass = MagicMock()
    mgr = SessionManager(hass)
    hass.data = {DOMAIN: {"sessions": mgr}}

    agent = Agent()
    result = await plan_execute("hi", list(agent.tools.values()), hass=hass, session_key=("c", "dev"))

    assert "Depth" in result
