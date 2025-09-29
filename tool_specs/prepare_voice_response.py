"""Tool for preparing voice-optimized responses."""

from __future__ import annotations

import logging
from typing import Any, Dict
import re

import voluptuous as vol

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

PARAMS = vol.Schema(
    {
        vol.Required("content"): str,
        vol.Optional("style", default="conversational"): vol.In([
            "conversational", "brief", "detailed", "confirmation", "informational"
        ]),
        vol.Optional("context"): dict,  # Optional context about what was asked
    }
)


async def prepare_voice_response(
    content: str,
    style: str = "conversational",
    context: dict | None = None,
    hass: Any | None = None,
) -> Dict[str, Any]:
    """
    Format content for natural voice output.
    
    This tool helps create responses that sound natural when spoken aloud,
    removing technical formatting and adapting tone based on the style.
    """
    log.debug(f"Preparing voice response, style={style}")
    
    # Clean up technical content for voice
    voice_content = content
    
    # Remove URLs (they don't speak well)
    voice_content = re.sub(r'https?://\S+', 'from the web', voice_content)
    
    # Replace entity IDs with friendly descriptions
    voice_content = re.sub(r'\blight\.[a-z_]+', 'the light', voice_content)
    voice_content = re.sub(r'\bswitch\.[a-z_]+', 'the switch', voice_content)
    voice_content = re.sub(r'\bsensor\.[a-z_]+', 'the sensor', voice_content)
    
    # Remove brackets and technical punctuation
    voice_content = re.sub(r'[\[\]{}]', '', voice_content)
    
    # Convert numbers and units for natural speech
    voice_content = re.sub(r'(\d+)%', r'\1 percent', voice_content)
    voice_content = re.sub(r'(\d+)°F', r'\1 degrees fahrenheit', voice_content)
    voice_content = re.sub(r'(\d+)°C', r'\1 degrees celsius', voice_content)
    
    # Apply style adjustments
    if style == "brief":
        # Truncate long responses
        sentences = voice_content.split('. ')
        if len(sentences) > 2:
            voice_content = '. '.join(sentences[:2]) + '.'
    
    elif style == "confirmation":
        # Ensure it sounds like a question
        if not voice_content.rstrip().endswith('?'):
            voice_content = voice_content.rstrip('.') + '?'
    
    elif style == "informational":
        # Add a natural intro if not present
        if not any(voice_content.lower().startswith(p) for p in ['according to', 'based on', 'i found']):
            voice_content = f"Based on my search, {voice_content}"
    
    # Clean up extra whitespace
    voice_content = ' '.join(voice_content.split())
    
    # Ensure proper ending punctuation
    if not voice_content.rstrip().endswith(('.', '!', '?')):
        voice_content += '.'
    
    return {
        "speak": voice_content,
        "original_content": content,
        "style": style,
        "kind": "response"  # Consistent with confirm_action pattern
    }


# Tool specification
SPEC = ToolSpec(
    name="prepare_voice_response",
    description=(
        "Format any content for natural voice output. Use this tool to prepare "
        "your final response to the user, ensuring it sounds natural when spoken aloud. "
        "This tool removes technical formatting, adapts tone, and optimizes for voice assistants."
    ),
    parameters=PARAMS,
    returns="dict with 'speak' field containing voice-optimized text",
    func=prepare_voice_response,
)
