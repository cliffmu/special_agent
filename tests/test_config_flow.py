import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import pytest

from special_agent.__init__ import async_setup_entry
from special_agent import session_store


@pytest.fixture(autouse=True)
def stub_http_registration(monkeypatch):
    # HTTP endpoint behavior has its own tests; these isolate config-entry setup.
    monkeypatch.setitem(sys.modules, "special_agent.live_api", SimpleNamespace(register_views=MagicMock()))


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
    entry = SimpleNamespace(
        entry_id="1",
        data={
            "openai_api_key": "key",
            "spotify_client_id": "cid",
            "spotify_client_secret": "secret",
        },
        options={},
    )
    asyncio.run(async_setup_entry(hass, entry))
    assert os.environ.get("OPENAI_API_KEY") == "key"
    assert os.environ.get("SPOTIFY_CLIENT_ID") == "cid"
    assert os.environ.get("SPOTIFY_CLIENT_SECRET") == "secret"


def test_setup_entry_without_spotify(monkeypatch):
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    monkeypatch.setattr(session_store, "Store", StubStore)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    entry = SimpleNamespace(entry_id="2", data={"openai_api_key": "key2"}, options={})
    asyncio.run(async_setup_entry(hass, entry))
    assert os.environ.get("OPENAI_API_KEY") == "key"
    assert os.environ.get("SPOTIFY_CLIENT_ID") is None
    assert os.environ.get("SPOTIFY_CLIENT_SECRET") is None
