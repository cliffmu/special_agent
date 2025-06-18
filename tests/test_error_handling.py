import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from special_agent.agent_core import Agent, plan_execute
from special_agent.session_store import SessionManager
from special_agent import DOMAIN


class FakeClient:
    def __init__(self, msgs):
        self._msgs = list(msgs)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *args, **kw):
        msg = self._msgs.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def make_err_msg():
    call = SimpleNamespace(
        id="1",
        function=SimpleNamespace(
            name="control_device",
            arguments=json.dumps(
                {
                    "service": "climate.set_temperature",
                    "data": {"entity_id": ["climate.main"], "temperature": -5},
                }
            ),
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


@pytest.mark.asyncio
async def test_control_device_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "1")
    msgs = [make_err_msg(), make_err_msg()]
    monkeypatch.setattr(
        "special_agent.utils.openai_client.get_async_client",
        lambda hass=None: FakeClient(msgs),
    )

    async def bad_control_device(*args, **kw):
        raise Exception(
            "Provided temperature -5 is not valid. Accepted range is 1 to 37"
        )

    from special_agent.tool_specs import control_device as cd

    monkeypatch.setattr(cd, "control_device", bad_control_device)
    monkeypatch.setattr(cd.SPEC, "func", bad_control_device)

    hass = MagicMock()
    mgr = SessionManager(hass)
    hass.data = {DOMAIN: {"sessions": mgr}}

    agent = Agent()
    result = await plan_execute(
        "hi", list(agent.tools.values()), hass=hass, session_key=("c", "dev")
    )

    assert "not valid" in str(result)
