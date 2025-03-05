"""
Tests for gpt_commands.py functionality
"""
import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the module to test
from gpt_commands import (
    classify_intent,
    generate_user_friendly_confirmation
)


class TestGptCommands(unittest.TestCase):
    """Test cases for gpt_commands.py functions"""

    @patch('gpt_commands.OpenAI')
    @patch('gpt_commands.log_to_file')
    def test_classify_intent(self, mock_log, mock_openai):
        """Test the classify_intent function with a mocked OpenAI client"""
        # Set up the mock
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        
        # Set up the mock completion
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "control"
        mock_client.chat.completions.create.return_value = mock_completion
        
        # Call the function
        result = classify_intent("Turn on the lights", api_key="fake_key")
        
        # Assertions
        self.assertEqual(result, "control")
        mock_client.chat.completions.create.assert_called_once()
        mock_log.assert_called_once()

    @patch('gpt_commands.OpenAI')
    @patch('gpt_commands.log_to_file')
    def test_classify_intent_fallback(self, mock_log, mock_openai):
        """Test classify_intent handles API errors gracefully"""
        # Set up the mock to raise an exception
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        
        # Call the function - should return "control" as default
        result = classify_intent("Turn on the lights", api_key="fake_key")
        
        # Assertions
        self.assertEqual(result, "control")
        mock_client.chat.completions.create.assert_called_once()

    @patch('gpt_commands.OpenAI')
    @patch('gpt_commands.log_to_file')
    def test_generate_user_friendly_confirmation(self, mock_log, mock_openai):
        """Test the generate_user_friendly_confirmation function"""
        # Set up the mock
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        
        # Set up the mock completion
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "Turn on office lights at 20%. Proceed?"
        mock_client.chat.completions.create.return_value = mock_completion
        
        # Test data
        user_text = "Turn on all the lights in my office to 20%"
        commands_list = [
            {
                "service": "light.turn_on",
                "data": {
                    "entity_id": "light.office_overhead",
                    "brightness_pct": 20
                }
            },
            {
                "service": "light.turn_on",
                "data": {
                    "entity_id": "light.office_desk",
                    "brightness_pct": 20
                }
            }
        ]
        
        # Call the function
        result = generate_user_friendly_confirmation(
            user_text, commands_list, api_key="fake_key"
        )
        
        # Assertions
        self.assertEqual(result, "Turn on office lights at 20%. Proceed?")
        mock_client.chat.completions.create.assert_called_once()
        mock_log.assert_called()

    def test_generate_user_friendly_confirmation_fallback(self):
        """Test fallback when no API key is provided"""
        commands_list = [
            {"service": "light.turn_on", "data": {"entity_id": "light.office_1"}},
            {"service": "light.turn_on", "data": {"entity_id": "light.office_2"}}
        ]
        
        result = generate_user_friendly_confirmation(
            "Turn on office lights", commands_list, api_key=None
        )
        
        # Should use fallback message
        self.assertEqual(result, "I found 2 devices to control. Shall I proceed?")


if __name__ == '__main__':
    unittest.main()