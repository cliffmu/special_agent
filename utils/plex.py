"""Plex utilities."""

from __future__ import annotations

import asyncio
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


async def companion_play_media(
    client_ip: str,
    rating_key: str,
    hass: HomeAssistant,
    verify_after_seconds: int = 4,
    plex_client_entity: str | None = None,
) -> Dict[str, Any]:
    """
    Play Plex content via Companion API (bypasses HA entity).
    
    This works even when HA entity shows 'unavailable'.
    
    Args:
        client_ip: Client device IP (e.g., '192.168.86.208')
        rating_key: Plex content rating key
        hass: Home Assistant instance
        verify_after_seconds: Wait time to verify playback started
        plex_client_entity: Optional HA entity to check state after
        
    Returns:
        Dict with status, state, and error/message
    """
    try:
        # Get Plex server details
        from .data_sources import get_integration_entry
        
        plex_entry = get_integration_entry(hass, "plex")
        if not plex_entry:
            return {"status": "error", "error": "Plex integration not configured"}
        
        data = dict(getattr(plex_entry, "data", {}) or {})
        server_config = data.get("server_config", {})
        
        # Extract token
        token = server_config.get("token") or data.get("token")
        if not token:
            server = data.get("server", {})
            token = server.get("token") if isinstance(server, dict) else None
        
        if not token:
            return {"status": "error", "error": "Plex token not found"}
        
        # Extract server ID
        server_id = data.get("server_id") or server_config.get("client_id")
        if not server_id:
            return {"status": "error", "error": "Plex server_id not found"}
        
        # Extract PMS IP
        pms_ip = server_config.get("url", "").replace("http://", "").replace("https://", "").split(":")[0]
        if not pms_ip:
            pms_ip = data.get("host", "127.0.0.1")
        
        log.debug(
            "companion_play_media: client_ip=%s, server_id=%s, pms_ip=%s",
            client_ip, server_id[:8] + "...", pms_ip
        )
        
        # HTTP requests
        try:
            import aiohttp
        except ImportError:
            return {"status": "error", "error": "aiohttp library not available"}
        
        async with aiohttp.ClientSession() as session:
            # Get client identifier
            try:
                async with session.get(
                    f"http://{client_ip}:32500/resources",
                    timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    if resp.status != 200:
                        return {
                            "status": "error",
                            "error": f"Client resources endpoint returned HTTP {resp.status}. Plex app may not be open or 'Announce as Player' not enabled."
                        }
                    
                    text = await resp.text()
                    import re
                    match = re.search(r'clientIdentifier="([^"]+)"', text)
                    if not match:
                        log.debug("companion_play_media: Resources response: %s", text[:200])
                        return {
                            "status": "error",
                            "error": "Device not announcing as Plex player. In Plex app on device: Settings → Network → Enable 'Advertise as player'"
                        }
                    
                    client_id = match.group(1)
                    log.debug("companion_play_media: Found client_id=%s", client_id[:8] + "...")
            
            except asyncio.TimeoutError:
                return {"status": "error", "error": "Timeout getting client ID - check client IP"}
            
            # Send play command
            play_url = (
                f"http://{client_ip}:32500/player/playback/playMedia"
                f"?key=/library/metadata/{rating_key}"
                f"&machineIdentifier={server_id}"
                f"&address={pms_ip}&port=32400&protocol=http"
                f"&token={token}"
            )
            
            try:
                async with session.get(
                    play_url,
                    headers={
                        "X-Plex-Client-Identifier": "special-agent",
                        "X-Plex-Target-Client-Identifier": client_id
                    },
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return {
                            "status": "error",
                            "error": f"Companion API returned {resp.status}: {error_text[:100]}"
                        }
                    
                    log.info("companion_play_media: Companion API success")
                    
                    # Wait and verify HA entity state (if provided)
                    await asyncio.sleep(verify_after_seconds)
                    final_state = "unknown"
                    if plex_client_entity:
                        state_obj = hass.states.get(plex_client_entity)
                        final_state = state_obj.state if state_obj else "unknown"
                    
                    return {
                        "status": "success",
                        "state": final_state,
                        "message": f"Playing via Companion API (state: {final_state})"
                    }
            
            except asyncio.TimeoutError:
                return {"status": "error", "error": "Companion API timeout"}
    
    except Exception as err:
        log.error("companion_play_media: Failed: %s", err, exc_info=True)
        return {"status": "error", "error": str(err)}


async def setup_and_play_plex(
    rating_key: str,
    plex_client_entity: str,
    client_ip: str | None,
    verify_after_seconds: int,
    hass: HomeAssistant,
) -> Dict[str, Any]:
    """
    Intelligently set up Plex and play content - skips steps already done.
    
    Checks states and only performs necessary actions:
    - Power on parent device if off
    - Select Plex source if not already selected
    - Scan for clients
    - Play content via Companion or HA service
    
    Args:
        rating_key: Plex content rating key
        plex_client_entity: Plex client media_player entity
        client_ip: Optional client IP (auto-discovered if None)
        verify_after_seconds: Verification delay
        hass: Home Assistant instance
        
    Returns:
        Dict with status, method, state, and steps_performed
    """
    steps_performed = []
    
    try:
        # 1. Find parent device entity (Apple TV, Roku, etc.)
        # Pattern: media_player.plex_plex_for_apple_tv_apple_tv_gym → media_player.gym_atv
        parent_entity = None
        if "plex_for" in plex_client_entity:
            # Extract area hint
            parts = plex_client_entity.split("_")
            area_hint = parts[-1] if len(parts) > 2 else None
            if area_hint:
                # Try common patterns
                for pattern in [f"media_player.{area_hint}_atv", f"media_player.{area_hint}_apple_tv", 
                               f"media_player.{area_hint}_roku", f"media_player.{area_hint}_tv"]:
                    if hass.states.get(pattern):
                        parent_entity = pattern
                        log.debug("setup_and_play_plex: Found parent device: %s", parent_entity)
                        break
        
        if not parent_entity:
            log.warning("setup_and_play_plex: Could not auto-discover parent device")
            return {
                "status": "error",
                "error": "Could not find parent device for Plex client. Provide setup manually."
            }
        
        # 2. Check parent device state and power on if needed
        parent_state = hass.states.get(parent_entity)
        if parent_state and parent_state.state == "off":
            log.info("setup_and_play_plex: Powering on %s", parent_entity)
            await hass.services.async_call(
                "media_player", "turn_on",
                {"entity_id": parent_entity},
                blocking=True
            )
            await asyncio.sleep(3)  # Wait for boot
            steps_performed.append("powered_on")
        else:
            log.debug("setup_and_play_plex: Parent device already on")
        
        # 3. Check if Plex app is selected source
        parent_state = hass.states.get(parent_entity)
        source_list = parent_state.attributes.get("source_list", []) if parent_state else []
        current_source = parent_state.attributes.get("source") if parent_state else None
        
        # Find Plex in source list
        plex_source = None
        for src in source_list:
            if "plex" in src.lower():
                plex_source = src
                break
        
        # Select Plex source if not already selected
        if plex_source and current_source != plex_source:
            log.info("setup_and_play_plex: Selecting source '%s' on %s", plex_source, parent_entity)
            await hass.services.async_call(
                "media_player", "select_source",
                {"entity_id": parent_entity, "source": plex_source},
                blocking=True
            )
            await asyncio.sleep(4)  # Wait for app to open
            steps_performed.append("opened_plex_app")
        else:
            log.debug("setup_and_play_plex: Plex app already selected")
        
        # 4. Scan for Plex clients (find scan button)
        # Search all button entities for scan clients (async-safe)
        scan_button = None
        
        def _find_scan_button():
            for state in hass.states.all():
                if state.domain == "button":
                    friendly_name = state.attributes.get("friendly_name", "").lower()
                    if "scan" in friendly_name and "client" in friendly_name:
                        return state.entity_id
            return None
        
        # Run in executor to avoid blocking event loop
        add_job = getattr(hass, "async_add_executor_job", None)
        if callable(add_job):
            scan_button = await add_job(_find_scan_button)
        else:
            scan_button = _find_scan_button()
        
        if scan_button:
            log.debug("setup_and_play_plex: Found scan button: %s", scan_button)
        
        if scan_button:
            log.info("setup_and_play_plex: Scanning for Plex clients")
            await hass.services.async_call(
                "button", "press",
                {"entity_id": scan_button},
                blocking=True
            )
            await asyncio.sleep(2)  # Wait for scan
            steps_performed.append("scanned_clients")
        
        # 5. Play content via Companion API (client likely still unavailable in HA)
        if not client_ip and parent_entity:
            from .data_sources import get_device_ip_from_entity
            client_ip = get_device_ip_from_entity(hass, parent_entity)
        
        if client_ip:
            log.info("setup_and_play_plex: Playing via Companion (IP: %s)", client_ip)
            result = await companion_play_media(
                client_ip, rating_key, hass, verify_after_seconds, plex_client_entity
            )
            result["steps_performed"] = steps_performed
            result["parent_device"] = parent_entity
            return result
        else:
            return {
                "status": "error",
                "error": "Could not discover client IP for Companion fallback",
                "steps_performed": steps_performed
            }
            
    except Exception as err:
        log.error("setup_and_play_plex: Failed: %s", err, exc_info=True)
        return {
            "status": "error",
            "error": str(err),
            "steps_performed": steps_performed
        }