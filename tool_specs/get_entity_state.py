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
            "oneOf": [
                {
                    "type": "string",
                    "description": "Entity ID or comma-separated list of entity IDs"
                },
                {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of entity IDs"
                },
            ],
            "description": "Entity ID(s) to query"
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
    entity_ids: str | List[str],
    attributes: List[str] | None = None,
    hass: Any | None = None,
) -> Dict[str, Any]:
    """Return state and selected attributes for the given entities.

    When ``attributes`` is ``None`` all available attributes from the
    entity's state are returned.
    """
    # Parse entity IDs provided as string, comma-separated string, or list-like
    entity_ids_list: List[str] = []
    if isinstance(entity_ids, str):
        parts = [part.strip() for part in entity_ids.split(",")]
        entity_ids_list.extend([part for part in parts if part])
    elif isinstance(entity_ids, (list, tuple, set)):
        for item in entity_ids:
            if not item:
                continue
            if isinstance(item, str):
                trimmed = item.strip()
            else:
                trimmed = str(item).strip()
            if trimmed:
                entity_ids_list.append(trimmed)
    else:
        trimmed = str(entity_ids).strip()
        if trimmed:
            entity_ids_list.append(trimmed)

    if not entity_ids_list:
        raise ValueError("No valid entity_ids provided")
    result: Dict[str, Any] = {}
    hass_states = getattr(hass, "states", None)
    get_state = getattr(hass_states, "get", None) if hass_states else None
    for eid in entity_ids_list:
        state = get_state(eid) if callable(get_state) else None
        if not state:
            result[eid] = {"state": "not_found", "error": "Entity does not exist"}
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
        
        entity_result = {"state": state.state, **attrs}

        # Flag unavailable states explicitly without adding noise for normal cases
        if state.state in ("unavailable", "unknown"):
            entity_result["available"] = False

        result[eid] = entity_result
    log.debug("get_entity_state -> %s", result)
    return result


SPEC = ToolSpec(
    name="get_entity_state",
    description=(
        "Return current state and attributes for entities. Returns {state, available, ...attributes}. "
        "Use this to: (1) Check if entity is available before controlling it, "
        "(2) Validate an action succeeded by checking state after, "
        "(3) Wait/delay for device startup (call this tool to introduce ~1-2 second delay while checking state). "
        "Media players: 'unavailable'=device off, 'idle'=on but nothing playing, 'playing'=active playback. "
        "The 'available' field is True unless state is 'unavailable' or 'unknown'."
    ),
    parameters=PARAMS,
    returns="dict of {entity_id: {state, available, ...attributes}}",
    func=get_entity_state,
    can_run_parallel=True,  # Read-only operation - safe for parallel execution
    can_run_in_sequence=True,
)
