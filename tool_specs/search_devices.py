"""Tool for retrieving device entity_ids by similarity search."""

from __future__ import annotations

import logging
from typing import List

import voluptuous as vol

from ..utils import logging as log
from ..utils.constants import EXCLUDED_DOMAINS, PREFERRED_DOMAINS, LOCATION_WORDS
from ..utils.vector_index import async_load_vector_index, query_vector_index
from ..agent_core import ToolSpec

_LOGGER = logging.getLogger(__package__)


PARAMS = vol.Schema(
    {
        vol.Required("query"): str,
        vol.Optional("k", default=5): int,
    }
)


async def search_devices(query: str, k: int = 5) -> List[str]:
    """Return entity_ids matching the query from the vector index."""
    index_data = await async_load_vector_index()
    raw_results = query_vector_index(index_data, query, k, return_scores=True)

    tokens = {t.rstrip('s') for t in query.lower().split()}
    scored = []
    for doc, score in raw_results:
        entity_id = doc["metadata"].get("entity_id", "")
        domain = entity_id.split(".")[0]
        if domain in PREFERRED_DOMAINS:
            score += 0.25
        if domain in EXCLUDED_DOMAINS:
            score -= 0.20

        friendly = (doc["metadata"].get("friendly_name") or "").lower()
        area = (doc["metadata"].get("area_id") or "").lower()
        base = f"{entity_id.lower()} {friendly} {area}"
        if any(
            word in LOCATION_WORDS and (word in friendly or word in area)
            for word in tokens
        ):
            score += 0.05

        # boost for direct text matches
        for token in tokens:
            if token and token in base:
                score += 0.05

        scored.append((score, entity_id))

    scored.sort(key=lambda x: x[0], reverse=True)
    log.debug("Device search '%s' => %s", query, [e for _, e in scored])
    return [e for _, e in scored]


SPEC = ToolSpec(
    name="search_devices",
    description="Find matching Home Assistant entities by text query.",
    parameters=PARAMS,
    returns="list of entity_ids",
    func=search_devices,
)
