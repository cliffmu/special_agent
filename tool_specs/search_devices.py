"""Tool for retrieving device entity_ids by filtered similarity search."""

from __future__ import annotations

import logging
from typing import List, Any

import voluptuous as vol

from ..utils.vector_index import (
    async_load_vector_index,
    async_query_vector_index,
)
from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)


PARAMS = vol.Schema(
    {
        vol.Required("query"): str,
        vol.Optional("area"): str,
        vol.Optional("domain", default=["light", "switch"]): vol.Any(str, [str]),
        vol.Optional("k", default=5): int,
    }
)


async def search_devices(
    query: str,
    area: str | None = None,
    domain: str | list[str] | None = None,
    k: int = 5,
    hass: Any | None = None,
) -> List[str]:
    """Return entity_ids matching the query with optional metadata filters."""
    index_data = await async_load_vector_index(hass=hass)
    filters: dict[str, Any] = {}
    if area:
        filters["area_id"] = area
    if domain:
        filters["domain"] = domain
    hits = await async_query_vector_index(
        index_data, query, k, filters, hass=hass
    )
    return [h["metadata"].get("entity_id", "") for h in hits]


SPEC = ToolSpec(
    name="search_devices",
    description="Find matching Home Assistant entities by text query.",
    parameters=PARAMS,
    returns="list of entity_ids",
    func=search_devices,
)
