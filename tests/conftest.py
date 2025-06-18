import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("SPECIAL_AGENT_TESTING", "true")

import sys
import types

sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
sys.modules.setdefault("homeassistant.core", MagicMock())
sys.modules.setdefault("homeassistant.config_entries", MagicMock())
helpers = types.ModuleType("homeassistant.helpers")
helpers.event = types.ModuleType("homeassistant.helpers.event")
helpers.event.async_track_time_interval = MagicMock()
helpers.storage = types.ModuleType("homeassistant.helpers.storage")
helpers.storage.Store = MagicMock()
helpers.intent = MagicMock()
helpers.area_registry = MagicMock()
helpers.device_registry = MagicMock()
helpers.entity_registry = MagicMock()
sys.modules.setdefault("homeassistant.helpers", helpers)
sys.modules.setdefault("homeassistant.helpers.event", helpers.event)
sys.modules.setdefault("homeassistant.helpers.storage", helpers.storage)
sys.modules.setdefault("homeassistant.helpers.area_registry", helpers.area_registry)
sys.modules.setdefault("homeassistant.helpers.device_registry", helpers.device_registry)
sys.modules.setdefault("homeassistant.helpers.entity_registry", helpers.entity_registry)
sys.modules.setdefault("homeassistant.helpers.intent", helpers.intent)
const_mod = types.ModuleType("homeassistant.const")
const_mod.EVENT_HOMEASSISTANT_STOP = "stop"
sys.modules.setdefault("homeassistant.const", const_mod)


@pytest.fixture
def hass():
    return MagicMock()
