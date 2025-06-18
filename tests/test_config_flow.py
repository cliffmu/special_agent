import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

from special_agent.__init__ import async_setup_entry
from special_agent import session_store


class StubStore:
    def __init__(self, *args, **kw):
        self._data = {}

    async def async_load(self):
        return self._data

    async def async_save(self, data):
        self._data = data


def test_setup_entry_sets_env(monkeypatch):
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    monkeypatch.setattr(session_store, "Store", StubStore)
    entry = SimpleNamespace(entry_id="1", data={"openai_api_key": "key"}, options={})
    asyncio.run(async_setup_entry(hass, entry))
    assert os.environ.get("OPENAI_API_KEY") == "key"
