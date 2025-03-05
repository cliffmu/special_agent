"""
Tests for agent_logic.py functionality

These tests use mocking to isolate the code from Home Assistant dependencies.
"""
import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import datetime

# Add parent directory to path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_logic import process_conversation_input, check_and_cleanup_sessions, handle_confirmation_phase


class TestAgentLogic(unittest.TestCase):
    """Test cases for agent_logic.py functions"""

    def setUp(self):
        """Set up test fixtures, if any."""
        # Create a mock Home Assistant instance
        self.mock_hass = MagicMock()
        self.mock_hass.data = {}
        
        # Mock configuration entries
        self.mock_hass.data["special_agent"] = {
            "config_entry_id": {
                "openai_api_key": "mock_api_key",
                "spotify_client_id": "mock_spotify_id",
                "spotify_client_secret": "mock_spotify_secret"
            }
        }

    @patch('agent_logic.classify_intent')
    def test_classify_intent_call(self, mock_classify):
        """Test that classify_intent is called with correct parameters"""
        mock_classify.return_value = "weather"  # Mock the return value
        
        # Call the function
        with patch('agent_logic.log_to_file') as mock_log:
            result, success = process_conversation_input("What's the weather like?", "device_1", self.mock_hass)
            
        # Verify classify_intent was called with correct arguments
        mock_classify.assert_called_once_with(
            "What's the weather like?", 
            api_key="mock_api_key"
        )
        
        # Verify expected result for weather intent
        self.assertEqual(result, "Weather not implemented")
        self.assertEqual(success, True)

    def test_session_cleanup(self):
        """Test the session cleanup functionality"""
        import datetime
        from special_agent.agent_logic import SESSION_TIMEOUT
        
        # Create a mock sessions dictionary
        sessions = {
            "device1": {
                "timestamp": datetime.datetime.now() - datetime.timedelta(seconds=SESSION_TIMEOUT + 10),
                "status": "awaiting_confirmation"
            },
            "device2": {
                "timestamp": datetime.datetime.now(),
                "status": "awaiting_confirmation"
            }
        }
        
        # Call the cleanup function
        cleaned = check_and_cleanup_sessions(sessions)
        
        # Verify it cleaned up the expired session
        self.assertEqual(cleaned, 1)
        self.assertNotIn("device1", sessions)
        self.assertIn("device2", sessions)

    @patch('agent_logic.execute_ha_command')
    @patch('agent_logic.log_command')
    @patch('agent_logic.log_to_file')
    def test_confirmation_phase_yes(self, mock_log, mock_command_log, mock_execute):
        """Test the confirmation phase with 'yes' response"""
        # Setup mocks
        mock_execute.return_value = True
        
        # Create pending dict for testing
        pending_dict = {}
        self.mock_hass.data["special_agent_pending"] = pending_dict
        
        # Create a pending command
        pending = {
            "commands_list": [
                {
                    "service": "light.turn_on",
                    "data": {
                        "entity_id": "light.living_room"
                    }
                }
            ],
            "status": "awaiting_confirmation"
        }
        
        # Test with 'yes' confirmation
        response, success = handle_confirmation_phase("yes", self.mock_hass, pending, "test_device")
        
        # Verify command was executed
        mock_execute.assert_called_once()
        self.assertEqual(response, "Done.")
        self.assertTrue(success)
        
        # Verify session was cleaned up
        self.assertEqual(len(pending_dict), 0)

    @patch('agent_logic.log_command')
    @patch('agent_logic.log_to_file')
    def test_confirmation_phase_no(self, mock_log, mock_command_log):
        """Test the confirmation phase with 'no' response"""
        # Create pending dict for testing
        pending_dict = {}
        self.mock_hass.data["special_agent_pending"] = pending_dict
        
        # Create a pending command
        pending = {
            "commands_list": [
                {
                    "service": "light.turn_on",
                    "data": {
                        "entity_id": "light.living_room"
                    }
                }
            ],
            "status": "awaiting_confirmation"
        }
        
        # Add the pending command to the dict
        pending_dict["test_device"] = pending
        
        # Test with 'no' confirmation
        response, success = handle_confirmation_phase("no", self.mock_hass, pending, "test_device")
        
        # Verify appropriate response and session cleanup
        self.assertEqual(response, "Request canceled.")
        self.assertTrue(success)
        self.assertEqual(len(pending_dict), 0)


if __name__ == '__main__':
    unittest.main()