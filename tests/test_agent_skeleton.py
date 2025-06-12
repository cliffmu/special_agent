import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent_core import Agent


def test_agent_plan_returns_placeholder():
    agent = Agent()
    response = asyncio.run(agent.plan("Hi"))
    assert "not ready" in response.lower() or "can't help" in response.lower()
