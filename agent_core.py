"""Core agent structures and stubs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

import voluptuous as vol


@dataclass
class ToolSpec:
    """Schema describing a tool."""

    name: str
    description: str
    parameters: vol.Schema
    returns: str | None
    func: Callable[..., Awaitable[Any]]


class Agent:
    """Minimal agent placeholder."""

    def __init__(self) -> None:
        self.tools: Dict[str, ToolSpec] = {}

    def register_tool(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    async def plan(self, user_input: str) -> str:
        return ""  # Not implemented yet

    async def execute_plan(self, plan: str) -> str:
        return ""  # Not implemented yet
