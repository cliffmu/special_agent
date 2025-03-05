"""
Tests for command_history.py functionality
"""
import unittest
from unittest.mock import patch, mock_open, MagicMock
import json
import sys
import os

# Add parent directory to path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the module to test
from command_history import log_command


class TestCommandHistory(unittest.TestCase):
    """Test cases for command_history.py functions"""

    @patch('command_history.open', new_callable=mock_open, read_data='[]')
    @patch('command_history.os.path.exists')
    @patch('command_history.log_to_file')
    def test_log_command_new_file(self, mock_log, mock_exists, mock_file):
        """Test logging a command when the history file doesn't exist yet"""
        # Setup
        mock_exists.return_value = False
        
        # Call the function
        log_command(
            user_text="Turn on the lights",
            device_id="device123",
            session_id="session456",
            command_response="Turning on the lights",
            success=True
        )
        
        # Assertions
        mock_file.assert_called()
        # Get the data written to the file
        write_call = mock_file().write.call_args[0][0]
        data = json.loads(write_call)
        
        # Verify it's a list with one entry
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["user_text"], "Turn on the lights")
        self.assertEqual(data[0]["device_id"], "device123")
        self.assertEqual(data[0]["session_id"], "session456")
        self.assertEqual(data[0]["response"], "Turning on the lights")
        self.assertEqual(data[0]["success"], True)
        self.assertIn("timestamp", data[0])

    @patch('command_history.open', new_callable=mock_open, 
           read_data='[{"timestamp": "2023-01-01T00:00:00", "user_text": "Old command"}]')
    @patch('command_history.os.path.exists')
    @patch('command_history.log_to_file')
    def test_log_command_existing_file(self, mock_log, mock_exists, mock_file):
        """Test logging a command when the history file already exists"""
        # Setup
        mock_exists.return_value = True
        
        # Call the function
        log_command(
            user_text="New command",
            device_id="device123",
            metadata={"test": "metadata"}
        )
        
        # Assertions
        mock_file.assert_called()
        # Get the data written to the file
        write_call = mock_file().write.call_args[0][0]
        data = json.loads(write_call)
        
        # Verify it's a list with two entries (old + new)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["user_text"], "Old command")
        self.assertEqual(data[1]["user_text"], "New command")
        self.assertEqual(data[1]["metadata"], {"test": "metadata"})

    @patch('command_history.open', new_callable=mock_open)
    @patch('command_history.os.path.exists')
    @patch('command_history.log_to_file')
    def test_log_command_with_commands_list(self, mock_log, mock_exists, mock_file):
        """Test logging a command with a commands_list included"""
        # Setup
        mock_exists.return_value = False
        
        commands_list = [
            {
                "service": "light.turn_on",
                "data": {
                    "entity_id": "light.living_room",
                    "brightness_pct": 50
                }
            }
        ]
        
        # Call the function
        log_command(
            user_text="Turn on the lights",
            device_id="device123",
            commands_list=commands_list,
            success=True
        )
        
        # Assertions
        mock_file.assert_called()
        # Get the data written to the file
        write_call = mock_file().write.call_args[0][0]
        data = json.loads(write_call)
        
        # Verify the commands were simplified and included
        self.assertEqual(len(data), 1)
        self.assertIn("commands", data[0])
        self.assertEqual(len(data[0]["commands"]), 1)
        self.assertEqual(data[0]["commands"][0]["service"], "light.turn_on")
        self.assertEqual(data[0]["commands"][0]["entity_id"], "light.living_room")


if __name__ == '__main__':
    unittest.main()