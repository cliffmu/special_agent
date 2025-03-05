"""
Pytest configuration file for Special Agent tests.

This file configures pytest for running tests against a Home Assistant custom component.
"""
import os
import sys
from unittest.mock import MagicMock

# Set environment variable to indicate we're in a test
os.environ['SPECIAL_AGENT_TESTING'] = 'true'

# Mock Home Assistant modules to allow imports to succeed without HA dependencies
sys.modules['homeassistant'] = MagicMock()
sys.modules['homeassistant.core'] = MagicMock()
sys.modules['homeassistant.config_entries'] = MagicMock()
sys.modules['homeassistant.components.conversation'] = MagicMock()
sys.modules['homeassistant.components'] = MagicMock()
sys.modules['homeassistant.helpers'] = MagicMock()
sys.modules['homeassistant.helpers.intent'] = MagicMock()

# Add pytest fixtures here as needed