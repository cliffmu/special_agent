import os
from unittest.mock import MagicMock

os.environ.setdefault('SPECIAL_AGENT_TESTING', 'true')

import sys
sys.modules.setdefault('homeassistant', MagicMock())
sys.modules.setdefault('homeassistant.core', MagicMock())
sys.modules.setdefault('homeassistant.helpers', MagicMock())
sys.modules.setdefault('homeassistant.helpers.intent', MagicMock())

import pytest

@pytest.fixture
def hass():
    return MagicMock()
