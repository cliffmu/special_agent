"""Search Spotify for a URI."""

from __future__ import annotations

import logging
from typing import Any

from ..agent_core import ToolSpec
from ..utils.spotify import search_spotify as _search
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

# OpenAI JSON schema format
PARAMS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search term for Spotify"
        },
        "type": {
            "type": "string",
            "enum": ["track", "artist", "album", "playlist"],
            "description": "Type of content to search for",
            "default": "track"
        }
    },
    "required": ["query"]
}


async def search_spotify(query: str, type: str = "track", hass: Any | None = None) -> str | None:
    """Return the Spotify URI for the given query."""
    uri = await _search(query, search_type=type, hass=hass)
    log.debug("search_spotify uri=%s", uri)
    return uri


SPEC = ToolSpec(
    name="search_spotify",
    description=(
        "Search Spotify and return the first result URI. "
        "The 'query' must be a plain text search term (do not pass a URI). "
        "The returned value is a string like 'spotify:<type>:<id>' or null if "
        "nothing is found. Use the returned URI as the media identifier for playback."
    ),
    parameters=PARAMS,
    returns="spotify uri or null",
    func=search_spotify,
    can_run_parallel=True,  # Read-only operation - safe for parallel execution
    can_run_in_sequence=False,  # Search in Call-1, execute in Call-2 (keeps scenes deterministic)
)
