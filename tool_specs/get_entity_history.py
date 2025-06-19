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

PARAMS = vol.Schema(
    {
        vol.Required("entity_id"): str,
        vol.Optional("lookback_hours", default=24): vol.All(
            int, vol.Range(min=1, max=168)
        ),
        vol.Optional("start_iso"): str,
        vol.Optional("end_iso"): str,
        vol.Optional("target_state"): str,
        vol.Optional("limit", default=1): vol.All(int, vol.Range(min=1)),
    }
)


async def get_entity_history(
    entity_id: str,
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
    description="Return recent state changes for entities.",
    parameters=PARAMS,
    returns="list of dict(state, when)",
    func=get_entity_history,
)
