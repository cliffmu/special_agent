"""Tool for retrieving device metadata by filtered similarity search."""

from __future__ import annotations

import logging
from typing import List, Any, Dict, Tuple

import re

from ..utils.vector_index import (
    async_load_device_index,
    async_query_vector_index,
)
from ..utils.constants import MAX_SEARCH_K
from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

_ATTR_RE = re.compile(r'["\']([^"\']+)["\']\s*:')


def _sanitize_info(text: str) -> Tuple[str, List[str] | None]:
    """Strip attribute values and return key names only."""
    if not text or "Attributes:" not in text:
        return text or "", None
    base, attr_str = text.split("Attributes:", 1)
    keys = _ATTR_RE.findall(attr_str)
    info = f"{base.strip()}\nAttributes:"
    return info, keys if keys else None


# OpenAI JSON schema format
PARAMS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Text query to search for matching devices"
        },
        "area": {
            "type": "string",
            "description": "Filter by area/room name"
        },
        "domain": {
            "type": "string",
            "description": "Filter by domain (e.g., 'light', 'switch', 'weather'). Use comma-separated for multiple.",
            "default": "light,switch"
        },
        "platform": {
            "type": "string",
            "description": "Optional filter by integration platform (e.g., 'plex', 'apple_tv'). Rarely needed."
        },
        "k": {
            "type": "integer",
            "description": f"Number of results to return (max {MAX_SEARCH_K})",
            "default": 5
        }
    },
    "required": ["query"]
}


async def search_devices(
    query: str,
    area: str | None = None,
    domain: str = "light,switch",
    platform: str | None = None,
    k: int = 5,
    hass: Any | None = None,
) -> List[Dict[str, Any]]:
    """Return matching devices with sanitized info and attribute keys."""
    k = min(k, MAX_SEARCH_K)
    index_data = await async_load_device_index(hass=hass)
    filters: dict[str, Any] = {}
    if area:
        # Normalize to lowercase for case-insensitive matching
        filters["area_id"] = area.lower().replace(" ", "_")
    if domain:
        # Parse comma-separated domain string to list
        domain_list = [d.strip() for d in domain.split(",")]
        filters["domain"] = domain_list
    if platform:
        filters["platform"] = platform
    hits = await async_query_vector_index(
        index_data, query, k, filters, hass=hass
    )
    results = []
    for doc in hits:
        meta = doc.get("metadata", {})
        info, attr_keys = _sanitize_info(doc.get("page_content", ""))
        item = {
            "entity_id": meta.get("entity_id", ""),
            "domain": meta.get("domain"),
            "area_id": meta.get("area_id"),
            "friendly_name": meta.get("friendly_name"),
            "platform": meta.get("platform"),  # Integration (plex, apple_tv, etc.)
            "info": info,
        }
        if attr_keys:
            item["attribute_keys"] = attr_keys
        results.append(item)
    return results


SPEC = ToolSpec(
    name="search_devices",
    description=(
        f"Find matching Home Assistant entities by text query. "
        f"Check the device list in your context to see if the requested device type exists before searching. "
        f"For weather queries, use domain='weather' to find weather entities - the 'temperature' attribute IS the current temp. "
        f"Use the area/domain filters and set k slightly larger than the count shown in the device list. "
        f"Results include platform (integration name like 'plex', 'apple_tv') to help identify entity types. "
        f"Parameter 'k' is capped at {MAX_SEARCH_K}."
    ),
    parameters=PARAMS,
    returns="list of device info dicts with entity_id, domain, area_id, friendly_name, platform, attribute_keys",
    func=search_devices,
    can_run_parallel=True,
)
