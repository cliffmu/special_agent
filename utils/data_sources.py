"""Helpers for retrieving Home Assistant data."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Tuple

try:  # Home Assistant may be absent during testing
    from homeassistant.config_entries import ConfigEntry
except ModuleNotFoundError:  # pragma: no cover - fallback stubs
    ConfigEntry = Any  # type: ignore

try:  # Home Assistant may be absent during testing
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
except ModuleNotFoundError:  # pragma: no cover - fallback stubs
    HomeAssistant = object  # type: ignore
    ar = dr = er = None  # type: ignore

from . import logging as log

_LOGGER = logging.getLogger(__package__)


def get_ha_states(hass: HomeAssistant) -> List[Dict]:
    """Return conversation-exposed states from Home Assistant.

    In addition to the basic state info, this function attempts to
    resolve the ``area_id`` for each entity via the entity and device
    registries.  When running tests or if the registries are not
    available, ``area_id`` will be ``None``.
    """
    log.debug("get_ha_states: fetching states")

    # Registries may be unavailable when running unit tests
    entity_reg = er.async_get(hass) if er else None
    device_reg = dr.async_get(hass) if dr else None

    devices: List[Dict] = []
    for state in hass.states.all():
        exposed = state.attributes.get("conversation_exposed", True)
        if not exposed:
            continue

        area_id = None
        platform = None
        if entity_reg and device_reg:
            ent_entry = entity_reg.entities.get(state.entity_id)
            if ent_entry:
                platform = getattr(ent_entry, "platform", None)
                if ent_entry.device_id:
                    dev_entry = device_reg.devices.get(ent_entry.device_id)
                    if dev_entry:
                        area_id = dev_entry.area_id

        devices.append(
            {
                "entity_id": state.entity_id,
                "name": state.name,
                "attributes": dict(state.attributes),
                "domain": state.domain,
                "area_id": area_id,
                "platform": platform,  # Integration providing this entity
            }
        )

    log.debug("get_ha_states: returning %d states", len(devices))
    return devices


def resolve_entity_metadata(
    hass: HomeAssistant, entity_id: str
) -> Tuple[str | None, str | None]:
    """Return ``(area_id, friendly_name)`` for an entity via registries."""
    area_id = None
    friendly_name = None

    try:
        state = hass.states.get(entity_id)
    except Exception:  # pragma: no cover - mocked hass may lack .states
        state = None

    entity_reg = er.async_get(hass) if er else None
    device_reg = dr.async_get(hass) if dr else None

    if entity_reg:
        ent_entry = entity_reg.entities.get(entity_id)
        if ent_entry:
            friendly_name = getattr(ent_entry, "original_name", None) or getattr(
                ent_entry, "name", None
            )
            if ent_entry.device_id and device_reg:
                dev_entry = device_reg.devices.get(ent_entry.device_id)
                if dev_entry:
                    area_id = dev_entry.area_id

    if area_id is None and state is not None:
        area_id = state.attributes.get("area_id")
    if friendly_name is None and state is not None:
        friendly_name = state.attributes.get("friendly_name") or state.name

    return area_id, friendly_name


def enrich_states_metadata(hass: HomeAssistant, states: Iterable[Dict]) -> List[Dict]:
    """Update a list of state dicts in place with area_id and friendly name."""
    result = []
    for st in states:
        area_id, friendly = resolve_entity_metadata(hass, st.get("entity_id", ""))
        if area_id is not None:
            st["area_id"] = area_id
        if friendly and isinstance(st.get("attributes"), dict):
            st.setdefault("attributes", {})
            st["attributes"].setdefault("friendly_name", friendly)
        result.append(st)
    return result


async def get_devices_by_area(hass: HomeAssistant) -> Tuple[Dict, List[Dict]]:
    """Return device registry info grouped by area."""
    log.debug("get_devices_by_area: start")
    area_reg = ar.async_get(hass) if ar else None
    device_reg = dr.async_get(hass) if dr else None
    entity_reg = er.async_get(hass) if er else None
    log.debug(
        "Registries available area=%s device=%s entity=%s",
        bool(area_reg),
        bool(device_reg),
        bool(entity_reg),
    )

    area_map = (
        {area.id: area.name for area in area_reg.areas.values()} if area_reg else {}
    )
    devices = device_reg.devices if device_reg else {}
    entities = entity_reg.entities if entity_reg else {}

    device_entities_map = defaultdict(list)
    for ent in entities.values():
        if ent.device_id:
            device_entities_map[ent.device_id].append(ent)

    summary: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    detail: List[Dict] = []

    for device_id, device_entry in devices.items():
        area_name = area_map.get(device_entry.area_id, "Unassigned")

        domains = {
            ent.entity_id.split(".")[0] for ent in device_entities_map[device_id]
        }

        detail.append(
            {
                "id": device_id,
                "name": device_entry.name or f"Device {device_id}",
                "area": area_name,
                "domains": list(domains),
                "manufacturer": device_entry.manufacturer,
                "model": device_entry.model,
            }
        )

        for domain in domains:
            summary[area_name][domain] += 1

    summary = {area: dict(domains) for area, domains in summary.items()}
    log.debug("get_devices_by_area: returning %d device details", len(detail))
    return summary, detail


def get_device_ip_from_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    """
    Extract device IP address from entity's device registry and config entries.
    
    Args:
        hass: Home Assistant instance
        entity_id: Entity ID to look up
        
    Returns:
        IP address string or None if not found
    """
    try:
        entity_reg = er.async_get(hass) if er else None
        device_reg = dr.async_get(hass) if dr else None
        
        if not entity_reg or not device_reg:
            return None
        
        # Get entity entry
        ent_entry = entity_reg.entities.get(entity_id)
        if not ent_entry or not ent_entry.device_id:
            return None
        
        # Get device entry
        dev_entry = device_reg.devices.get(ent_entry.device_id)
        if not dev_entry:
            return None
        
        # PRIORITY: Check config entries (most reliable for Apple TV, etc.)
        config_entries = getattr(hass, "config_entries", None)
        if config_entries and dev_entry.config_entries:
            for entry_id in dev_entry.config_entries:
                try:
                    entries = config_entries.async_entries()
                    for entry in entries:
                        if getattr(entry, "entry_id", None) == entry_id:
                            # Check entry.data for address/host/ip
                            data = dict(getattr(entry, "data", {}) or {})
                            for key in ["address", "host", "ip", "hostname"]:
                                addr = data.get(key)
                                if addr and isinstance(addr, str) and "." in addr and addr.count(".") == 3:
                                    try:
                                        parts = addr.split(".")
                                        if all(0 <= int(p) <= 255 for p in parts):
                                            log.debug("get_device_ip: Found IP %s for %s via config_entry.data['%s']", 
                                                     addr, entity_id, key)
                                            return addr
                                    except (ValueError, AttributeError):
                                        pass
                except Exception as err:
                    log.debug("get_device_ip: Error checking config_entry %s: %s", entry_id, err)
        
        # Fallback: Check connections for IP
        for conn_type, conn_id in dev_entry.connections:
            if conn_type == "mac":
                continue
            conn_str = str(conn_id)
            if "." in conn_str and conn_str.count(".") == 3:
                try:
                    parts = conn_str.split(".")
                    if all(0 <= int(p) <= 255 for p in parts):
                        log.debug("get_device_ip: Found IP %s for %s via connection %s", conn_str, entity_id, conn_type)
                        return conn_str
                except (ValueError, AttributeError):
                    pass
        
        # Fallback: Check identifiers
        for id_type, id_val in dev_entry.identifiers:
            id_str = str(id_val)
            if "." in id_str and id_str.count(".") == 3:
                try:
                    parts = id_str.split(".")
                    if all(0 <= int(p) <= 255 for p in parts):
                        log.debug("get_device_ip: Found IP %s for %s via identifier %s", id_str, entity_id, id_type)
                        return id_str
                except (ValueError, AttributeError):
                    pass
        
        log.debug("get_device_ip: No IP found for %s", entity_id)
        return None
        
    except Exception as err:
        log.debug("get_device_ip: Error for %s: %s", entity_id, err)
        return None


def get_integration_entry(hass: HomeAssistant, domain: str) -> ConfigEntry | None:
    """Return the first config entry for ``domain`` if available."""

    if hass is None:
        log.debug("get_integration_entry: hass is None for domain=%s", domain)
        return None

    config_entries = getattr(hass, "config_entries", None)
    if config_entries is None:
        log.debug(
            "get_integration_entry: hass has no config_entries attribute for domain=%s",
            domain,
        )
        return None

    try:
        entries = config_entries.async_entries(domain)  # type: ignore[attr-defined]
    except Exception as err:  # pragma: no cover - defensive
        log.error(
            "get_integration_entry: failed to fetch entries for domain=%s: %s",
            domain,
            err,
        )
        return None

    if not entries:
        log.debug("get_integration_entry: no entries for domain=%s", domain)
        return None

    entry = entries[0]
    entry_id = getattr(entry, "entry_id", "unknown")
    log.debug("get_integration_entry: using entry_id=%s for domain=%s", entry_id, domain)
    return entry


def get_plex_connection_info(hass: HomeAssistant) -> Tuple[str, str]:
    """Return ``(base_url, token)`` required to connect to Plex."""

    entry = get_integration_entry(hass, "plex")
    if entry is None:
        raise RuntimeError("Plex integration is not configured")

    data: Dict[str, Any] = dict(getattr(entry, "data", {}) or {})
    options: Dict[str, Any] = dict(getattr(entry, "options", {}) or {})

    # Debug: log what keys are actually present
    log.debug(
        "get_plex_connection_info: entry.data keys=%s, entry.options keys=%s",
        list(data.keys()),
        list(options.keys()),
    )

    # Try multiple possible locations for the token
    token = (
        data.get("token")
        or options.get("token")
        or data.get("server_id")  # Common in newer Plex integration
        or options.get("server_id")
    )
    
    # If still not found, check if there's a nested server dict
    if not token:
        server = data.get("server") or options.get("server")
        if isinstance(server, dict):
            token = server.get("token") or server.get("accessToken")
    
    if not token:
        log.error(
            "get_plex_connection_info: token not found. Available data keys: %s, options keys: %s",
            list(data.keys()),
            list(options.keys()),
        )
        raise RuntimeError("Plex token missing from config entry")

    base_url = _extract_plex_base_url(data, options)
    if not base_url:
        log.error(
            "get_plex_connection_info: base_url not found. Available data keys: %s, options keys: %s",
            list(data.keys()),
            list(options.keys()),
        )
        raise RuntimeError("Plex base URL missing from config entry")

    log.debug(
        "get_plex_connection_info: resolved base_url=%s (masked) for entry_id=%s",
        base_url[:20] + "..." if len(base_url) > 20 else base_url,
        getattr(entry, "entry_id", "unknown"),
    )
    return base_url, token


def _extract_plex_base_url(data: Dict[str, Any], options: Dict[str, Any]) -> str | None:
    """Best-effort extraction of the Plex server base URL."""

    # Check top-level keys first
    for key in ("base_url", "url"):
        url = data.get(key) or options.get(key)
        if isinstance(url, str) and url:
            return url

    # Check inside server dict
    server = data.get("server") or options.get("server")
    if isinstance(server, dict):
        log.debug("_extract_plex_base_url: server dict keys=%s", list(server.keys()))
        for key in ("uri", "url", "baseurl", "base_url", "address", "local_address"):
            url = server.get(key)
            if isinstance(url, str) and url:
                log.debug("_extract_plex_base_url: found url in server[%s]=%s", key, url[:30])
                return url

    # Check inside server_config dict (common in newer Plex integration)
    server_config = data.get("server_config") or options.get("server_config")
    if isinstance(server_config, dict):
        log.debug("_extract_plex_base_url: server_config dict keys=%s", list(server_config.keys()))
        for key in ("uri", "url", "baseurl", "base_url", "address", "local_address"):
            url = server_config.get(key)
            if isinstance(url, str) and url:
                log.debug("_extract_plex_base_url: found url in server_config[%s]=%s", key, url[:30])
                return url

    # Try to construct from host/port
    host = data.get("host") or options.get("host")
    if not host and isinstance(server, dict):
        host = server.get("host") or server.get("address")
    if not host and isinstance(server_config, dict):
        host = server_config.get("host") or server_config.get("address")
    
    if isinstance(host, str) and host:
        ssl = bool(data.get("ssl") or options.get("ssl"))
        port = (
            data.get("port")
            or options.get("port")
            or (server.get("port") if isinstance(server, dict) else None)
            or (server_config.get("port") if isinstance(server_config, dict) else None)
        )
        try:
            port_int = int(port) if port else None
        except (TypeError, ValueError):
            port_int = None
        if not port_int:
            port_int = 32400 if not ssl else 443
        scheme = "https" if ssl or port_int == 443 else "http"
        constructed_url = f"{scheme}://{host}:{port_int}"
        log.debug("_extract_plex_base_url: constructed url from host/port=%s", constructed_url[:30])
        return constructed_url

    return None
