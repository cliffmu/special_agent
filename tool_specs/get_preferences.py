"""Tool for reading preference records."""

import json
from pathlib import Path
from typing import Any, Dict

from ..agent_core import ToolSpec
from ..utils import logging as log

PREFS_FILE = Path("/config/.special_agent_prefs.json")

PARAMS = {
    "type": "object",
    "properties": {
        "namespace": {
            "type": "string",
            "description": "Preference namespace: 'screens', 'scenes', 'sequences'"
        },
        "key": {
            "type": "string",
            "description": "Specific key to retrieve (optional - omit to get all in namespace)"
        }
    },
    "required": ["namespace"]
}

async def get_preferences(
    namespace: str,
    key: str | None = None,
    hass: Any | None = None
) -> Dict[str, Any]:
    """
    Retrieve preferences from storage.
    
    Returns:
    - If key specified: single record
    - If no key: all records in namespace
    """
    log.debug(f"get_preferences: namespace={namespace}, key={key}")
    
    if not PREFS_FILE.exists():
        return {"status": "empty", "data": {}}
    
    with open(PREFS_FILE) as f:
        prefs = json.load(f)
    
    ns_data = prefs.get(namespace, {})
    
    if key:
        return {"status": "ok", "data": ns_data.get(key, {})}
    else:
        return {"status": "ok", "data": ns_data}

SPEC = ToolSpec(
    name="get_preferences",
    description=(
        "Read preference records for screens, scenes, or sequences. "
        "Omit 'key' to get all records in a namespace. "
        "Use for: discovering scenes, loading screen wiring, checking saved configurations."
    ),
    parameters=PARAMS,
    returns="dict with status and data",
    func=get_preferences
)

