"""Return the live state and attributes for one or more entities."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

# OpenAI JSON schema format
PARAMS = {
    "type": "object",
    "properties": {
        "entity_ids": {
            "type": "string",
            "description": "Entity ID or comma-separated list of entity IDs"
        },
        "attributes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of specific attributes to return (optional)"
        }
    },
    "required": ["entity_ids"]
}


async def get_entity_state(
    entity_ids: str,
    attributes: List[str] | None = None,
    hass: Any | None = None,
) -> Dict[str, Any]:
    """Return state and selected attributes for the given entities.

    When ``attributes`` is ``None`` all available attributes from the
    entity's state are returned.
    """
    # Parse comma-separated entity IDs
    if "," in entity_ids:
        entity_ids_list = [eid.strip() for eid in entity_ids.split(",")]
    else:
        entity_ids_list = [entity_ids]
    result: Dict[str, Any] = {}
    hass_states = getattr(hass, "states", None)
    get_state = getattr(hass_states, "get", None) if hass_states else None
    for eid in entity_ids_list:
        state = get_state(eid) if callable(get_state) else None
        if not state:
            result[eid] = None
            continue
        attr_keys = attributes or list(state.attributes)
        attrs = {}
        for k in attr_keys:
            if k in state.attributes:
                val = state.attributes.get(k)
                # Convert enums to their string value for JSON serialization
                if hasattr(val, 'value'):
                    attrs[k] = val.value
                else:
                    attrs[k] = val
        result[eid] = {"state": state.state, **attrs}
    log.debug("get_entity_state -> %s", result)
    return result


SPEC = ToolSpec(
    name="get_entity_state",
    description=(
        "Return current state and requested attributes for entities. "
        "Parameter 'entity_ids' accepts a single string or list of strings; "
        "a single value will be wrapped into a list."
    ),
    parameters=PARAMS,
    returns="dict of entity states",
    func=get_entity_state,
    can_run_parallel=True,  # Read-only operation - safe for parallel execution
)
