"""
Tests for entity_refinement.py functionality
"""
import unittest
from unittest.mock import patch
import sys
import os

# Add parent directory to path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the module to test
from entity_refinement import (
    filter_irrelevant_entities, 
    rerank_and_filter_docs,
    extract_domain
)


class TestEntityRefinement(unittest.TestCase):
    """Test cases for entity_refinement.py functions"""

    def test_filter_irrelevant_entities(self):
        """Test filtering out irrelevant entities"""
        # Sample input
        all_states = [
            {"entity_id": "light.kitchen", "domain": "light", "name": "Kitchen Light"},
            {"entity_id": "number.led_brightness", "domain": "number", "name": "LED Brightness"},
            {"entity_id": "input_number.volume", "domain": "input_number", "name": "Volume"},
            {"entity_id": "person.john", "domain": "person", "name": "John"},
            {"entity_id": "automation.morning", "domain": "automation", "name": "Morning Routine"}
        ]
        
        # Call the function
        with patch('entity_refinement.log_to_file'):
            result = filter_irrelevant_entities(all_states)
        
        # Check results
        # Should filter out automation domain and LED brightness
        expected_entity_ids = ["light.kitchen", "input_number.volume", "person.john"]
        self.assertEqual(len(result), len(expected_entity_ids))
        for entity in result:
            self.assertIn(entity["entity_id"], expected_entity_ids)

    def test_rerank_and_filter_docs(self):
        """Test the reranking and filtering of documents"""
        # Sample docs
        docs = [
            {"page_content": "Entity: light.living_room\nName: Living Room Light", 
             "metadata": {"entity_id": "light.living_room"}},
            {"page_content": "Entity: light.bedroom\nName: Bedroom Light", 
             "metadata": {"entity_id": "light.bedroom"}},
            {"page_content": "Entity: sensor.temperature\nName: Temperature Sensor", 
             "metadata": {"entity_id": "sensor.temperature"}},
            {"page_content": "Entity: media_player.living_room\nName: Living Room Speaker", 
             "metadata": {"entity_id": "media_player.living_room"}},
        ]
        
        # Test with living room in query
        with patch('entity_refinement.log_to_file'):
            result = rerank_and_filter_docs("Turn on the living room lights", docs, filter_qty=2)
        
        # Living room light and speaker should be ranked higher
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["metadata"]["entity_id"], "light.living_room")
        self.assertEqual(result[1]["metadata"]["entity_id"], "media_player.living_room")
        
        # Test with bedroom in query
        with patch('entity_refinement.log_to_file'):
            result = rerank_and_filter_docs("Turn on the bedroom lights", docs, filter_qty=2)
        
        # Bedroom light should be ranked higher
        self.assertEqual(result[0]["metadata"]["entity_id"], "light.bedroom")

    def test_extract_domain(self):
        """Test extracting domain from entity document"""
        # Test with entity_id in metadata
        doc = {
            "page_content": "Test content",
            "metadata": {"entity_id": "light.living_room"}
        }
        self.assertEqual(extract_domain(doc), "light")
        
        # Test with domain in metadata
        doc = {
            "page_content": "Test content",
            "metadata": {"entity_id": "something", "domain": "media_player"}
        }
        self.assertEqual(extract_domain(doc), "media_player")
        
        # Test with no domain info
        doc = {
            "page_content": "Test content",
            "metadata": {"other_field": "value"}
        }
        self.assertEqual(extract_domain(doc), "unknown")


if __name__ == '__main__':
    unittest.main()