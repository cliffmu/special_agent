"""Helpers for retrieving Home Assistant data."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Tuple

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
        if entity_reg and device_reg:
            ent_entry = entity_reg.entities.get(state.entity_id)
            if ent_entry and ent_entry.device_id:
                dev_entry = device_reg.devices.get(ent_entry.device_id)
                if dev_entry:
                    area_id = dev_entry.area_id

        devices.append(
            {
                "entity_id": state.entity_id,
                "name": state.name,
                "attributes": state.attributes,
                "domain": state.domain,
                "area_id": area_id,
            }
        )

    log.debug("get_ha_states: returning %d states", len(devices))
    return devices


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
