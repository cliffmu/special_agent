"""Tool for saving/updating preference records."""

import json
from pathlib import Path
from typing import Any, Dict
from datetime import datetime

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
            "description": "Key to save under (e.g., 'gym_movie', 'gym')"
        },
        "patch": {
            "type": "object",
            "description": "Data to save or merge"
        },
        "mode": {
            "type": "string",
            "enum": ["merge", "replace"],
            "description": "Merge with existing or replace entirely",
            "default": "merge"
        }
    },
    "required": ["namespace", "key", "patch"]
}

async def set_preferences(
    namespace: str,
    key: str,
    patch: Dict[str, Any],
    mode: str = "merge",
    hass: Any | None = None
) -> Dict[str, Any]:
    """
    Save or update a preference record.
    
    SPECIAL HANDLING for scenes namespace:
    - If 'entities' field present → updates both preferences AND HA scene
    - If only metadata (keywords, description) → updates preferences only
    """
    log.debug(f"set_preferences: namespace={namespace}, key={key}, mode={mode}")
    
    # Load existing preferences
    if PREFS_FILE.exists():
        with open(PREFS_FILE) as f:
            prefs = json.load(f)
    else:
        prefs = {"version": 2}
    
    # Ensure namespace exists
    if namespace not in prefs:
        prefs[namespace] = {}
    
    # Merge or replace
    if mode == "merge" and key in prefs[namespace]:
        existing = prefs[namespace][key]
        prefs[namespace][key] = {**existing, **patch}
    else:
        prefs[namespace][key] = patch
    
    # Add timestamp
    prefs[namespace][key]["updated"] = datetime.now().isoformat()
    
    # ★ CRITICAL: Sync HA scene if device states are being updated
    if namespace == "scenes" and hass:
        scene_data = prefs[namespace][key]
        ha_scene_id = scene_data.get("ha_scene_id", f"scene.{key}")
        
        # If patch includes entities (device states), update HA scene
        if "entities" in patch:
            log.info(f"Updating HA scene {ha_scene_id} with new device states")
            
            # Build entity list for snapshot
            entities_to_snapshot = []
            if isinstance(patch["entities"], dict):
                entities_to_snapshot = list(patch["entities"].keys())
            elif isinstance(patch["entities"], list):
                entities_to_snapshot = patch["entities"]
            
            # Update HA scene
            await hass.services.async_call(
                "scene", "create",
                {
                    "scene_id": ha_scene_id.split(".")[-1],
                    "snapshot_entities": entities_to_snapshot
                },
                blocking=True
            )
            log.debug(f"HA scene {ha_scene_id} updated with {len(entities_to_snapshot)} entities")
    
    # Save preferences file
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PREFS_FILE, 'w') as f:
        json.dump(prefs, f, indent=2)
    
    return {"status": "saved", "key": key, "namespace": namespace}

SPEC = ToolSpec(
    name="set_preferences",
    description=(
        "Save or update preferences for screens, scenes, or sequences. "
        "Use mode='merge' to update specific fields, 'replace' for full overwrite. "
        "IMPORTANT: When updating scenes with 'entities' field, automatically syncs HA scene. "
        "Use for: learning screen wiring, saving scenes, updating keywords."
    ),
    parameters=PARAMS,
    returns="dict with status",
    func=set_preferences
)

