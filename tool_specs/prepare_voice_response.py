"""Tool for preparing voice-optimized responses."""

from __future__ import annotations

import logging
from typing import Any, Dict
import re

from ..agent_core import ToolSpec
from ..utils import logging as log

_LOGGER = logging.getLogger(__package__)

# OpenAI JSON schema format
PARAMS = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": "The content to format for voice output"
        },
        "style": {
            "type": "string",
            "enum": ["conversational", "brief", "detailed", "confirmation", "informational"],
            "description": "Output style",
            "default": "conversational"
        },
        "context": {
            "type": "object",
            "description": "Optional context about the query"
        }
    },
    "required": ["content"]
}


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
    
    # Apply style adjustments BEFORE unit conversion
    if style == "brief":
        # For brief style, keep it very concise
        # Remove bullet points
        voice_content = re.sub(r'[•\-]\s*', '', voice_content)
        # Remove line breaks
        voice_content = voice_content.replace('\n', ' ')
        # Remove "about" and similar hedging words
        voice_content = re.sub(r'\babout\s+', '', voice_content)
        # Keep % symbol for brevity (don't convert to "percent")
        # Limit to 2-3 key sentences
        sentences = [s.strip() for s in voice_content.split('.') if s.strip()]
        if len(sentences) > 2:
            voice_content = '. '.join(sentences[:2]) + '.'
        else:
            voice_content = '. '.join(sentences) + '.'
    else:
        # For other styles, convert units to words
        voice_content = re.sub(r'(\d+)%', r'\1 percent', voice_content)
        voice_content = re.sub(r'(\d+)°F', r'\1 degrees fahrenheit', voice_content)
        voice_content = re.sub(r'(\d+)°C', r'\1 degrees celsius', voice_content)
    
    if style == "confirmation":
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
        "Format content for natural voice output. ALWAYS use this for final answers to users. "
        "IMPORTANT: For style='brief', provide a SHORT SUMMARY (1-2 sentences max), NOT detailed lists. "
        "Example brief: 'You have 5 office lights on, most at full brightness' NOT listing each light individually. "
        "This tool removes technical formatting, adapts tone, and optimizes for voice assistants. "
        "Valid styles: 'conversational' (default), 'brief', 'detailed', 'confirmation', 'informational'. "
        "Do NOT use this for confirm_action or ask_user - they format their own output."
    ),
    parameters=PARAMS,
    returns="dict with 'speak' field containing voice-optimized text",
    func=prepare_voice_response,
    can_run_parallel=False,  # Final answer - should run alone
)
