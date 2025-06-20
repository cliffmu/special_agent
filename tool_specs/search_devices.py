"""Tool for retrieving device entity_ids by filtered similarity search."""

from __future__ import annotations

import logging
from typing import List, Any, Dict

import voluptuous as vol

from ..utils.vector_index import (
    async_load_vector_index,
    async_query_vector_index,
)
from ..utils.constants import MAX_SEARCH_K
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
) -> List[Dict[str, Any]]:
    """Return matching devices with full metadata and text info."""
    k = min(k, MAX_SEARCH_K)
    index_data = await async_load_vector_index(hass=hass)
    filters: dict[str, Any] = {}
    if area:
        filters["area_id"] = area
    if domain:
        filters["domain"] = domain
    hits = await async_query_vector_index(
        index_data, query, k, filters, hass=hass
    )
    results = []
    for doc in hits:
        meta = doc.get("metadata", {})
        results.append(
            {
                "entity_id": meta.get("entity_id", ""),
                "domain": meta.get("domain"),
                "area_id": meta.get("area_id"),
                "friendly_name": meta.get("friendly_name"),
                "info": doc.get("page_content"),
            }
        )
    return results


SPEC = ToolSpec(
    name="search_devices",
    description=(
        f"Find matching Home Assistant entities by text query. "
        f"This tool *always* needs a non-empty 'query' string (e.g. 'lights'). "
        f"Parameter 'k' is capped at {MAX_SEARCH_K}."
    ),
    parameters=PARAMS,
    returns="list of device info dicts",
    func=search_devices,
)
