"""Core agent structures and stubs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

try:  # Home Assistant may be absent when running tests
    from homeassistant.core import HomeAssistant
except ModuleNotFoundError:  # pragma: no cover - fallback stub
    HomeAssistant = object  # type: ignore

import voluptuous as vol

try:
    from .utils import data_sources
except ImportError:  # pragma: no cover - support running as script
    from utils import data_sources  # type: ignore

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

    def __init__(self, hass: Optional[HomeAssistant] = None) -> None:
        _LOGGER.debug("Agent Init")
        self.tools: Dict[str, ToolSpec] = {}
        self.hass: Optional[HomeAssistant] = hass

    def register_tool(self, spec: ToolSpec) -> None:
        _LOGGER.debug("Agent Register Tool")
        self.tools[spec.name] = spec

    async def plan(self, user_input: str) -> str:
        """Return a placeholder response until tools are added."""
        _LOGGER.debug("Agent Plan")
        if self.hass is not None:
            try:
                states = data_sources.get_ha_states(self.hass)
                _LOGGER.debug("plan: retrieved %d states", len(states))
                summary, detail = await data_sources.get_devices_by_area(self.hass)
                _LOGGER.debug("plan: device summary %s", summary)
                _LOGGER.debug("plan: device detail count %d", len(detail))
            except Exception as exc:  # pragma: no cover - debug path
                _LOGGER.debug("plan: error fetching HA data: %s", exc)
        else:
            _LOGGER.debug("plan: no hass instance available")
        return "I'm not ready to help yet."

    async def execute_plan(self, plan: str) -> str:
        _LOGGER.debug("Agent Execute Plan")
        """Execute a planned sequence of tool calls (stub)."""
        return ""
