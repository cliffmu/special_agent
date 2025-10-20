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
    """Return Plex search hits and any connection errors."""

    result = await _plex_search(hass, query=query, kind=kind)
    hits = result.get("hits", [])
    error = result.get("error")
    
    log.debug("search_plex: query=%s kind=%s hits=%d error=%s", query, kind, len(hits), error)
    
    return {"hits": hits, "error": error}


SPEC = ToolSpec(
    name="search_plex_library",
    description=(
        "Search the connected Plex library and return rating keys for playback. "
        "Use the 'ratingKey' with play_plex_media tool (NOT media_player.play_media - use the dedicated tool). "
        "IMPORTANT: Results show what's ACTUALLY IN THE LIBRARY - if user asks for 'latest' episode, "
        "check year/season/episode in results. The highest season/episode returned IS the latest available. "
        "Don't search for future seasons/years - Plex only has what's downloaded. "
        "Example: Results show 'Season 27 Episode 10 (2002)' → that IS the latest, don't search '2025'. "
        "If user wants newer: 'Your latest episode is S27E10 from 2002. Ready to play it?' "
        "Fire off parallel searches for title variations. If 'error' field present, connection failed."
    ),
    parameters=PARAMS,
    returns="{hits: array, error: string|null}",
    func=search_plex,
    can_run_parallel=True,
    can_run_in_sequence=True,
)
