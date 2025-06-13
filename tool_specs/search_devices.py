"""Tool for retrieving device entity_ids by similarity search."""
from __future__ import annotations

from typing import List

import voluptuous as vol

from ..utils.vector_index import load_vector_index, query_vector_index
from ..agent_core import ToolSpec


PARAMS = vol.Schema(
    {
        vol.Required("query"): str,
        vol.Optional("k", default=5): int,
    }
)


async def search_devices(query: str, k: int = 5) -> List[str]:
    """Return entity_ids matching the query from the vector index."""
    index_data = load_vector_index()
    results = query_vector_index(index_data, query, k)
    return [doc["metadata"]["entity_id"] for doc in results]


SPEC = ToolSpec(
    name="search_devices",
    description="Find matching Home Assistant entities by text query.",
    parameters=PARAMS,
    returns="list of entity_ids",
    func=search_devices,
)
