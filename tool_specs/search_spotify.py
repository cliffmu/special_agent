"""Search Spotify for a URI."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from ..agent_core import ToolSpec
from ..utils.spotify import search_spotify as _search
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)


PARAMS = vol.Schema(
    {
        vol.Required("query"): str,
        vol.Optional("type", default="track"): vol.In(["track", "artist", "album", "playlist"]),
    }
)


async def search_spotify(query: str, type: str = "track", hass: Any | None = None) -> str | None:
    """Return the Spotify URI for the given query."""
    uri = await _search(query, search_type=type, hass=hass)
    log.debug("search_spotify uri=%s", uri)
    return uri


SPEC = ToolSpec(
    name="search_spotify",
    description="Search Spotify and return the first result URI.",
    parameters=PARAMS,
    returns="spotify uri or null",
    func=search_spotify,
)
