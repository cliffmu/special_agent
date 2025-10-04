"""Minimal Spotify Web API helpers."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import aiohttp

from . import logging as log

_LOGGER = logging.getLogger(__package__)

SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"
_TOKEN: dict[str, Any] = {"access_token": None, "expires_at": 0}

_SEARCH_TYPE_KEY_MAP = {
    "track": "tracks",
    "artist": "artists",
    "album": "albums",
    "playlist": "playlists",
}


async def get_spotify_access_token(hass: Any | None = None) -> str | None:
    """Return a cached Spotify access token using Client Credentials flow."""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        log.debug("Spotify credentials missing")
        return None

    now = time.time()
    if _TOKEN.get("access_token") and now < _TOKEN["expires_at"] - 60:
        return _TOKEN["access_token"]

    token_url = "https://accounts.spotify.com/api/token"
    data = {"grant_type": "client_credentials"}
    auth = aiohttp.BasicAuth(client_id.strip(), client_secret.strip())

    async with aiohttp.ClientSession() as session:
        async with session.post(token_url, data=data, auth=auth) as resp:
            if resp.status == 200:
                payload = await resp.json()
                _TOKEN["access_token"] = payload.get("access_token")
                _TOKEN["expires_at"] = now + payload.get("expires_in", 0)
                return _TOKEN["access_token"]
            log.debug("Spotify token error: %s - %s", resp.status, await resp.text())
            return None


async def search_spotify(
    query: str,
    search_type: str = "track",
    limit: int = 5,  # Increased from 1 - Spotify sometimes returns [None] with limit=1
    market: str = "US",
    hass: Any | None = None,
) -> str | None:
    """Search Spotify and return the first result URI."""
    try:
        token = await get_spotify_access_token(hass)
        if not token:
            log.debug("Spotify token unavailable")
            return None

        headers = {"Authorization": f"Bearer {token}"}
        params = {"q": query, "type": search_type, "limit": limit, "market": market}

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{SPOTIFY_API_BASE_URL}/search", headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    log.debug("Spotify search response for '%s': %s", query, data)
                    plural = _SEARCH_TYPE_KEY_MAP.get(search_type, f"{search_type}s")
                    
                    # Handle case where data.get(plural) returns None instead of dict
                    result = data.get(plural) or {}
                    items = result.get("items", [])
                    
                    # Filter out None items (Spotify sometimes returns [None])
                    valid_items = [item for item in items if item is not None and isinstance(item, dict)]
                    
                    if valid_items:
                        uri = valid_items[0].get("uri")
                        log.debug("Spotify found URI: %s", uri)
                        return uri
                    
                    # Log why we didn't find anything
                    if items and not valid_items:
                        log.warning(
                            "Spotify API returned %d items for '%s' but all were None/invalid. "
                            "This may indicate an API issue or auth problem. Total results: %d",
                            len(items), query, result.get("total", 0)
                        )
                    else:
                        log.debug("Spotify search returned no items for '%s'", query)
                    return None
                log.debug("Spotify search error: %s - %s", resp.status, await resp.text())
                return None
    except Exception as e:
        log.error("Spotify search exception for '%s': %s", query, e, exc_info=True)
        return None
