from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict
import time

try:
    from homeassistant.helpers.storage import Store
except Exception:  # pragma: no cover - during unit tests
    class Store:  # type: ignore
        def __init__(self, *args, **kw):
            self._data = {}

        async def async_load(self):
            return self._data

        async def async_save(self, data):
            self._data = data

try:
    from .utils import logging as log
except Exception:  # pragma: no cover - direct execution
    from utils import logging as log


@dataclass
class Session:
    """Dataclass for a persisted conversation session."""

    messages: list
    pending: Dict[str, Any] | None
    focus: Dict[str, Any] | None
    device_id: str
    updated: float


class SessionManager:
    """Manage persisted sessions for multiple devices."""

    def __init__(self, hass) -> None:
        self._hass = hass
        self._store = Store(hass, 1, ".special_agent_sessions.json")
        self._data: Dict[str, Session] = {}

    async def load(self) -> None:
        data = await self._store.async_load() or {}
        self._data = {k: Session(**v) for k, v in data.items()}
        log.debug("SessionManager loaded %s sessions", len(self._data))

    async def save(self) -> None:
        await self._store.async_save({k: asdict(v) for k, v in self._data.items()})
        log.debug("SessionManager saved %s sessions", len(self._data))

    @staticmethod
    def _fmt(key: tuple[str, str] | str) -> str:
        if isinstance(key, tuple):
            return f"{key[0]}|{key[1]}"
        return key

    def get(self, key: tuple[str, str] | str) -> Session | None:
        return self._data.get(self._fmt(key))

    def set(self, key: tuple[str, str] | str, session: Session) -> None:
        self._data[self._fmt(key)] = session

    def pop(self, key: tuple[str, str] | str) -> Session | None:
        return self._data.pop(self._fmt(key), None)

    def clear_expired(self, ttl: int = 300) -> None:
        cutoff = time.time() - ttl
        for k in list(self._data):
            if self._data[k].updated < cutoff:
                log.debug("Session expired: %s", k)
                self._data.pop(k)
