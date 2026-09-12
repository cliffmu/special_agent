from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict
import asyncio
from contextlib import asynccontextmanager
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


class SessionBusyError(RuntimeError):
    """The bounded registry has no room for another active conversation."""


@dataclass
class _RequestLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class SessionRequestLocks:
    """Serialize one history's readers/writers; release keys after all waiters leave."""

    def __init__(self, max_keys=256):
        self._entries = {}
        self._max_keys = max_keys

    @asynccontextmanager
    async def hold(self, key):
        # HA calls this on its event loop; count waiters before the first await.
        entry = self._entries.get(key)
        if entry is None:
            if len(self._entries) >= self._max_keys:
                raise SessionBusyError("Too many concurrent conversations")
            entry = self._entries[key] = _RequestLock()
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0:
                del self._entries[key]


def session_request_lock(hass, entry_id, session_key):
    """Share locks across entity reloads without serializing unrelated devices."""
    state = hass.data.setdefault("special_agent", {})
    if "session_request_locks" not in state:
        state["session_request_locks"] = SessionRequestLocks()
    return state["session_request_locks"].hold((entry_id, *session_key))


class SessionManager:
    """Manage persisted sessions for multiple devices."""

    def __init__(self, hass) -> None:
        self._hass = hass
        self._store = Store(hass, 1, ".special_agent_sessions.json")
        self._data: Dict[str, Session] = {}
        self._save_lock = asyncio.Lock()

    async def load(self) -> None:
        data = await self._store.async_load() or {}
        self._data = {k: Session(**v) for k, v in data.items()}
        log.debug("SessionManager loaded %s sessions", len(self._data))

    async def save(self) -> None:
        # Different conversations may finish together. Snapshot inside the write
        # lock so a slower old snapshot can never replace newer device history.
        async with self._save_lock:
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
