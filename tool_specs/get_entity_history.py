"""Return recent state changes for an entity from the recorder history."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

import voluptuous as vol

try:
    import homeassistant.util.dt as dt_util
    from homeassistant.util.dt import utcnow as utc_now
    as_local = dt_util.as_local
    parse_datetime = dt_util.parse_datetime
except Exception:  # pragma: no cover - not available in unit test env
    def utc_now() -> datetime:  # type: ignore
        return datetime.utcnow()

    class dt_util:  # type: ignore
        @staticmethod
        def as_local(dt: datetime) -> datetime:
            return dt

        @staticmethod
        def parse_datetime(val: str) -> datetime:
            return datetime.fromisoformat(val)

    as_local = dt_util.as_local  # type: ignore
    parse_datetime = dt_util.parse_datetime  # type: ignore

from ..agent_core import ToolSpec
from ..utils import logging as log

try:  # allow import failure during tests
    from homeassistant.components.history import get_significant_states
except Exception:  # pragma: no cover - not available in unit test env
    get_significant_states = None

_LOGGER = logging.getLogger(__package__)

MAX_EVENTS = 100

# OpenAI JSON schema format
PARAMS = {
    "type": "object",
    "properties": {
        "entity_id": {
            "oneOf": [
                {
                    "type": "string",
                    "description": "Entity ID to get history for"
                },
                {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of entity IDs (first item is used)"
                },
            ],
            "description": "Entity ID to get history for"
        },
        "lookback_hours": {
            "type": "integer",
            "description": "Hours to look back (1-168)",
            "default": 24,
            "minimum": 1,
            "maximum": 168
        },
        "start_iso": {
            "type": "string",
            "description": "Start time in ISO format (optional)"
        },
        "end_iso": {
            "type": "string",
            "description": "End time in ISO format (optional)"
        },
        "target_state": {
            "type": "string",
            "description": "Filter for specific state value (optional)"
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of events to return",
            "default": 1,
            "minimum": 1
        }
    },
    "required": ["entity_id"]
}


async def get_entity_history(
    entity_id: str | List[str],
    lookback_hours: int = 24,
    start_iso: str | None = None,
    end_iso: str | None = None,
    target_state: str | None = None,
    limit: int = 1,
    hass: Any | None = None,
) -> List[Dict[str, Any]]:
    """Return a simplified list of state changes for an entity.

    The Recorder history resets on Home Assistant restart. Results are capped
    at ``MAX_EVENTS`` to avoid returning excessively large payloads.
    """
    if get_significant_states is None:
        raise RuntimeError("history component not available")
    # entity_id may be provided as list from legacy scenes/tests - use first element
    if isinstance(entity_id, list):
        entity_id = entity_id[0] if entity_id else ""
    if start_iso or end_iso:
        start = parse_datetime(start_iso) if start_iso else None
        end = parse_datetime(end_iso) if end_iso else None
    else:
        start = utc_now() - timedelta(hours=lookback_hours)
        end = None
    hist = await hass.async_add_executor_job(
        get_significant_states, hass, start, end, [entity_id], True
    )
    events = hist.get(entity_id, [])
    if target_state:
        events = [e for e in events if e.state == target_state]
    limit = min(limit, MAX_EVENTS)
    events = events[-limit:]
    result = [
        {"state": e.state, "when": as_local(e.last_changed).isoformat()} for e in events
    ]
    log.debug("get_entity_history -> %s", result)
    return result


SPEC = ToolSpec(
    name="get_entity_history",
    description=(
        "Return recent state changes for an entity from recorder history. "
        "Use to analyze patterns, verify past actions, or check device behavior over time. "
        "History resets on Home Assistant restart."
    ),
    parameters=PARAMS,
    returns="list of dict(state, when)",
    func=get_entity_history,
    can_run_parallel=True,  # Read-only operation - safe for parallel execution
    can_run_in_sequence=True,
)
