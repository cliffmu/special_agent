"""Return the live state and attributes for one or more entities."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import voluptuous as vol

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

PARAMS = vol.Schema(
    {
        vol.Required("entity_ids"): vol.All([str], vol.Length(min=1)),
        vol.Optional("attributes"): [str],
    }
)


async def get_entity_state(
    entity_ids: List[str],
    attributes: List[str] | None = None,
    hass: Any | None = None,
) -> Dict[str, Any]:
    """Return state and selected attributes for the given entities."""
    result: Dict[str, Any] = {}
    hass_states = getattr(hass, "states", None)
    get_state = getattr(hass_states, "get", None) if hass_states else None
    for eid in entity_ids:
        state = get_state(eid) if callable(get_state) else None
        if not state:
            result[eid] = None
            continue
        attr_keys = attributes or list(state.attributes)
        attrs = {k: state.attributes.get(k) for k in attr_keys if k in state.attributes}
        result[eid] = {"state": state.state, **attrs}
    log.debug("get_entity_state -> %s", result)
    return result


SPEC = ToolSpec(
    name="get_entity_state",
    description="Return current state and requested attributes for entities.",
    parameters=PARAMS,
    returns="dict of entity states",
    func=get_entity_state,
)
