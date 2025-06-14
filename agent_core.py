"""Core agent structures and stubs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

import voluptuous as vol

_LOGGER = logging.getLogger("custom_components.special_agent")

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
        _LOGGER.debug("Agent Init")
        self.tools: Dict[str, ToolSpec] = {}

    def register_tool(self, spec: ToolSpec) -> None:
        _LOGGER.debug("Agent Register Tool")
        self.tools[spec.name] = spec

    async def plan(self, user_input: str) -> str:
        """Return a placeholder response until tools are added."""
        _LOGGER.debug("Agent Plan")
        return "I'm not ready to help yet."

    async def execute_plan(self, plan: str) -> str:
        _LOGGER.debug("Agent Execute Plan")
        """Execute a planned sequence of tool calls (stub)."""
        return ""
