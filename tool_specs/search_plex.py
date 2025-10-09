"""Search Plex for library items and return rating keys."""

from __future__ import annotations

from typing import Any, Dict

from ..agent_core import ToolSpec
from ..utils.plex import plex_search as _plex_search
from ..utils import logging as log

PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search term to use against the Plex library",
        },
        "kind": {
            "type": "string",
            "enum": ["movie", "show", "episode"],
            "description": "Optional Plex media type hint",
        },
    },
    "required": ["query"],
}


async def search_plex(query: str, kind: str | None = None, hass: Any | None = None) -> Dict[str, Any]:
    """Return Plex search hits."""

    hits = await _plex_search(hass, query=query, kind=kind)
    log.debug("search_plex: query=%s kind=%s hits=%d", query, kind, len(hits))
    return {"hits": hits}


SPEC = ToolSpec(
    name="search_plex_library",
    description=(
        "Search the connected Plex library and return rating keys for playback. "
        "Use the 'ratingKey' with media_player.play_media (media_content_type='plex'). "
        "Fire off additional searches (the tool can run in parallel) when you need to "
        "check multiple title variations."
    ),
    parameters=PARAMS,
    returns="{hits: array}",
    func=search_plex,
    can_run_parallel=True,
)
