"""Plex utilities."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

try:  # Home Assistant may be absent during testing
    from homeassistant.core import HomeAssistant
except ModuleNotFoundError:  # pragma: no cover - fallback stubs
    HomeAssistant = Any  # type: ignore

from . import logging as log
from .data_sources import get_plex_connection_info

_LOGGER = logging.getLogger(__package__)

_SEASON_EPISODE_RE = re.compile(r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,2})\b", re.IGNORECASE)


async def plex_search(
    hass: HomeAssistant | None,
    query: str,
    kind: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Search Plex for media matching ``query``.
    
    Returns dict with 'hits' (list) and optional 'error' (str) fields.
    """

    if not query:
        log.debug("plex_search: empty query")
        return {"hits": [], "error": None}

    if hass is None:
        log.error("plex_search: hass instance is required")
        return {"hits": [], "error": "Home Assistant instance not available"}

    try:
        base_url, token = get_plex_connection_info(hass)
    except Exception as err:  # pragma: no cover - defensive
        error_msg = str(err)
        log.error("plex_search: unable to resolve Plex connection info: %s", error_msg)
        return {"hits": [], "error": f"Plex connection error: {error_msg}"}

    log.debug(
        "plex_search: query=%s kind=%s limit=%s base_url_resolved=%s",
        query,
        kind,
        limit,
        bool(base_url),
    )

    def _run_search() -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Returns (hits, error_message)."""
        try:
            from plexapi.exceptions import NotFound  # type: ignore
            from plexapi.server import PlexServer  # type: ignore
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency missing
            log.error("plex_search: plexapi not installed: %s", exc)
            return [], "PlexAPI library not installed"

        try:
            server = PlexServer(base_url, token)
        except Exception as err:  # pragma: no cover - network issues
            log.error("plex_search: failed to connect to Plex server: %s", err)
            return [], f"Failed to connect to Plex server: {err}"

        machine_id = getattr(server, "machineIdentifier", None)
        results: List[Any] = []

        season_episode = _SEASON_EPISODE_RE.search(query)
        cleaned_query = query
        if season_episode:
            cleaned_query = _SEASON_EPISODE_RE.sub("", query).strip()

        try:
            search_kwargs = {"limit": max(limit * 2, 10)}
            if kind:
                search_kwargs["mediatype"] = kind
            results.extend(server.search(query, **search_kwargs))
        except Exception as err:  # pragma: no cover - API errors
            log.error("plex_search: search failed: %s", err)
            return [], f"Plex search failed: {err}"

        if season_episode:
            try:
                season = int(season_episode.group("season"))
                episode = int(season_episode.group("episode"))
            except (TypeError, ValueError):
                season = episode = None

            if season is not None and episode is not None:
                try:
                    shows = server.search(cleaned_query or query, mediatype="show")
                except Exception as err:  # pragma: no cover - API errors
                    log.error("plex_search: show lookup failed: %s", err)
                    shows = []

                for show in shows:
                    try:
                        ep = show.episode(season=season, episode=episode)
                    except NotFound:
                        continue
                    except Exception as err:  # pragma: no cover - API errors
                        log.error("plex_search: episode lookup failed: %s", err)
                        continue
                    if ep:
                        results.insert(0, ep)
                        break

        seen_keys = set()
        hits: List[Dict[str, Any]] = []

        for item in results:
            rating_key = getattr(item, "ratingKey", None)
            if not rating_key or rating_key in seen_keys:
                continue
            seen_keys.add(rating_key)

            media_type = getattr(item, "type", None) or getattr(item, "TYPE", None)
            media_type = str(media_type) if media_type else None

            hits.append(
                {
                    "title": getattr(item, "title", None),
                    "type": media_type,
                    "year": _safe_int(getattr(item, "year", None)),
                    "ratingKey": str(rating_key),
                    "grandparentTitle": getattr(item, "grandparentTitle", None),
                    "season": _safe_int(
                        getattr(item, "seasonNumber", None)
                        or getattr(item, "parentIndex", None)
                    ),
                    "episode": _safe_int(
                        getattr(item, "episodeNumber", None)
                        or getattr(item, "index", None)
                    ),
                    "server_machineIdentifier": machine_id,
                }
            )

            if len(hits) >= limit:
                break

        return hits, None  # Success: return hits with no error

    if hasattr(hass, "async_add_executor_job"):
        try:
            hits, error = await hass.async_add_executor_job(_run_search)
            return {"hits": hits, "error": error}
        except Exception as err:  # pragma: no cover - defensive
            log.error("plex_search: executor job failed: %s", err)
            return {"hits": [], "error": f"Executor job failed: {err}"}

    hits, error = _run_search()
    return {"hits": hits, "error": error}


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
