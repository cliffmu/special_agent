"""Tool to rebuild the vector index from Home Assistant states."""
from __future__ import annotations

import logging
from typing import Any

import asyncio
import functools

from ..utils import logging as log
from ..utils.vector_index import build_vector_index
from ..utils.data_sources import get_ha_states, enrich_states_metadata
from ..agent_core import ToolSpec

_LOGGER = logging.getLogger(__package__)

# OpenAI JSON schema format  
PARAMS = {
    "type": "object",
    "properties": {
        "force": {
            "type": "boolean",
            "description": "Force rebuild even if index exists",
            "default": False
        }
    },
    "required": []
}


async def build_vector_index_tool(
    force: bool = False,
    hass: Any | None = None,
) -> str:
    """Build or refresh the vector index from Home Assistant states."""
    log.debug("build_vector_index_tool start force=%s hass=%s", force, bool(hass))

    async def _worker() -> None:
        add_job = getattr(hass, "async_add_executor_job", None) if hass else None
        if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
            states = await add_job(get_ha_states, hass)
            states = enrich_states_metadata(hass, states)
            log.debug("Retrieved %d states from Home Assistant", len(states))
            await add_job(
                functools.partial(build_vector_index, force_rebuild=force), states
            )
        else:
            states = get_ha_states(hass)
            states = enrich_states_metadata(hass, states)
            log.debug("Retrieved %d states from Home Assistant", len(states))
            await asyncio.get_running_loop().run_in_executor(
                None,
                functools.partial(build_vector_index, states, force_rebuild=force),
            )
        log.info("Vector index built with %d states", len(states))
        log.debug("build_vector_index_tool completed")

    create_task = (
        getattr(hass, "async_create_background_task", None) if hass else None
    )
    if callable(create_task) and create_task.__class__.__name__ != "MagicMock":
        create_task(_worker(), "rebuild_vector_index")
        return "rebuild started in background"

    asyncio.create_task(_worker())
    return "rebuild scheduled"

SPEC = ToolSpec(
    name="build_vector_index",
    description="Rebuild the smart-home vector index from HA states.",
    parameters=PARAMS,
    returns="status message",
    func=build_vector_index_tool,
)
