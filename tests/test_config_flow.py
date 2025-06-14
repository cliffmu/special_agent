import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import pytest

from special_agent.__init__ import async_setup_entry


def test_setup_entry_sets_env(monkeypatch):
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    entry = SimpleNamespace(entry_id="1", data={"openai_api_key": "key"}, options={})
    asyncio.run(async_setup_entry(hass, entry))
    assert os.environ.get("OPENAI_API_KEY") == "key"
