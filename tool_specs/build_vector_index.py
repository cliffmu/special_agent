"""Tool to rebuild the vector index from Home Assistant states."""
from __future__ import annotations

import logging
from typing import Any, Iterable, Dict

import voluptuous as vol

from ..utils import logging as log
from ..utils.vector_index import build_vector_index
from ..utils.data_sources import get_ha_states
from ..agent_core import ToolSpec

_LOGGER = logging.getLogger(__package__)

PARAMS = vol.Schema({vol.Optional("force", default=False): bool})


async def build_vector_index_tool(
    force: bool = False,
    hass: Any | None = None,
) -> str:
    """Build or refresh the vector index from Home Assistant states."""
    states = get_ha_states(hass) if hass else []
    build_vector_index(states, force_rebuild=force)
    log.info("Vector index built with %d states", len(states))
    return "rebuilt"

SPEC = ToolSpec(
    name="build_vector_index",
    description="Rebuild the smart-home vector index from HA states.",
    parameters=PARAMS,
    returns="rebuilt",
    func=build_vector_index_tool,
)
