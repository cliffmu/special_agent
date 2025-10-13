# Scene Management via Preferences System

## Overview

Voice-controlled scene management using the unified preference system. Scenes are stored as HA-native scenes with metadata in the preferences file, discoverable and activatable through existing tools.

---

## Architecture

### Core Principles

1. **HA Scenes as Source of Truth**: Device states stored in native HA scenes (UI-editable)
2. **Preferences for Metadata**: Scene keywords, descriptions stored in `.special_agent_prefs.json`
3. **Reuse Existing Tools**: No dedicated scene tools - use `get_preferences`, `set_preferences`, `control_device`
4. **Integration Ready**: Scenes can be referenced in screen playback sequences (`pre_scene` field)

### Data Storage

**Home Assistant Scenes:**
```yaml
# Created via scene.create service, editable in HA UI
scene.gym_movie:
  entities:
    light.overhead: "off"
    light.bias: {brightness: 51}
    fan.ceiling: {percentage: 30}
```

**Preferences File:**
```json
{
  "version": 2,
  "scenes": {
    "gym_movie": {
      "ha_scene_id": "scene.gym_movie",
      "description": "Movie watching in gym",
      "trigger_keywords": ["movie", "film", "watch"],
      "areas": ["gym"],
      "device_count": 8,
      "created_by": "voice_agent",
      "created_date": "2025-10-02T14:30:00",
      "last_used": "2025-10-02T20:15:00"
    }
  }
}
```

---

## Tool Implementations Needed

### Tool 1: get_preferences

**File:** `tool_specs/get_preferences.py`

```python
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
```

### Tool 2: set_preferences

**File:** `tool_specs/set_preferences.py`

```python
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
    
    Special handling for scenes namespace:
    - If updating scene device states, also updates HA scene
    - If just updating metadata (keywords, description), only updates preferences
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
        prefs[namespace][key] = {**prefs[namespace][key], **patch}
    else:
        prefs[namespace][key] = patch
    
    # Add timestamp
    prefs[namespace][key]["updated"] = datetime.now().isoformat()
    
    # SPECIAL: If updating scene with device changes, update HA scene too
    if namespace == "scenes" and "entities" in patch and hass:
        scene_id = prefs[namespace][key].get("ha_scene_id", f"scene.{key}")
        log.debug(f"Updating HA scene {scene_id} with new states")
        
        # Update HA scene with new states
        await hass.services.async_call(
            "scene", "create",
            {
                "scene_id": scene_id.split(".")[-1],
                "snapshot_entities": list(patch["entities"].keys()) if isinstance(patch["entities"], dict) else patch["entities"]
            },
            blocking=True
        )
    
    # Save preferences file
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PREFS_FILE, 'w') as f:
        json.dump(prefs, f, indent=2)
    
    return {"status": "saved", "key": key}

SPEC = ToolSpec(
    name="set_preferences",
    description=(
        "Save or update preference records. "
        "Use mode='merge' to update specific fields, 'replace' to overwrite entirely. "
        "When updating scenes with device states, automatically updates the HA scene too. "
        "Use for: saving scenes, learning screen wiring, updating configurations."
    ),
    parameters=PARAMS,
    returns="dict with status",
    func=set_preferences
)
```

### Tool 3: run_sequence

**File:** `tool_specs/run_sequence.py`

```python
"""Tool for executing multi-step sequences with waits and guards."""

import asyncio
import time
from typing import Any, Dict, List
import re

from ..agent_core import ToolSpec
from ..utils import logging as log

PARAMS = {
    "type": "object",
    "properties": {
        "sequence_ref": {
            "type": "string",
            "description": "Reference to saved sequence in preferences (e.g., 'play_on_screen.v1')"
        },
        "sequence": {
            "type": "object",
            "description": "Inline sequence definition with steps array"
        },
        "vars": {
            "type": "object",
            "description": "Variables for ${substitution} in sequence steps"
        },
        "timeout": {
            "type": "integer",
            "description": "Overall timeout in seconds",
            "default": 30
        }
    },
    "required": []
}

def _substitute_vars(obj: Any, vars: Dict) -> Any:
    """Replace ${variable} placeholders in strings, dicts, and lists."""
    if isinstance(obj, str):
        def replacer(match):
            var_name = match.group(1)
            return str(vars.get(var_name, ""))
        return re.sub(r'\$\{([a-zA-Z0-9_?]+)\}', replacer, obj)
    elif isinstance(obj, dict):
        return {k: _substitute_vars(v, vars) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_substitute_vars(item, vars) for item in obj]
    return obj

async def _call_service(hass: Any, service: str, data: Dict) -> None:
    """Call a Home Assistant service."""
    domain, name = service.split(".", 1)
    await hass.services.async_call(domain, name, data, blocking=True)

async def _wait_state(
    hass: Any,
    entity_id: str,
    in_states: List[str] | None = None,
    not_in: List[str] | None = None,
    attr: str | None = None,
    equals: Any | None = None,
    timeout: int = 10
) -> bool:
    """Wait for entity to reach desired state."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        state_obj = hass.states.get(entity_id)
        if state_obj:
            value = state_obj.state if not attr else state_obj.attributes.get(attr)
            
            if in_states and value in in_states:
                return True
            if not_in and value not in not_in:
                return True
            if equals is not None and value == equals:
                return True
        
        await asyncio.sleep(0.5)
    return False

async def run_sequence(
    sequence_ref: str | None = None,
    sequence: Dict | None = None,
    vars: Dict | None = None,
    timeout: int = 30,
    hass: Any | None = None
) -> Dict[str, Any]:
    """
    Execute a sequence of steps with variable substitution.
    
    Steps can include:
    - service_call: Call HA service
    - wait_state: Wait for entity state
    - delay: Simple time delay
    - if: Conditional execution
    
    Returns per-step status and overall result.
    """
    log.debug(f"run_sequence: ref={sequence_ref}, inline={bool(sequence)}")
    
    # Load sequence from preferences if ref provided
    if sequence_ref:
        from .get_preferences import get_preferences
        result = await get_preferences(namespace="sequences", key=sequence_ref, hass=hass)
        seq_data = result.get("data", {})
        steps = seq_data.get("steps", [])
    else:
        steps = (sequence or {}).get("steps", [])
    
    vars = vars or {}
    results = []
    started = time.monotonic()
    
    for step in steps:
        if time.monotonic() - started > timeout:
            return {
                "status": "error",
                "result": "timeout",
                "steps": results,
                "error": "Sequence timeout exceeded"
            }
        
        step_result = {
            "name": step.get("name") or step.get("service") or step.get("type"),
            "status": "pending"
        }
        
        try:
            # Check conditional guards
            if_guard = step.get("only_if")
            if if_guard and not vars.get(if_guard.strip("${}"), False):
                step_result["status"] = "skipped"
                results.append(step_result)
                continue
            
            # Check state guards
            only_if_state = step.get("only_if_state")
            if only_if_state:
                entity = _substitute_vars(only_if_state["entity_id"], vars)
                state_obj = hass.states.get(entity)
                if state_obj:
                    current_state = state_obj.state
                    in_states = only_if_state.get("in")
                    not_in_states = only_if_state.get("not_in")
                    
                    if in_states and current_state not in in_states:
                        step_result["status"] = "skipped"
                        results.append(step_result)
                        continue
                    if not_in_states and current_state in not_in_states:
                        step_result["status"] = "skipped"
                        results.append(step_result)
                        continue
            
            # Execute step based on type
            step_type = step["type"]
            
            if step_type == "service_call":
                service = step["service"]
                data = _substitute_vars(step.get("data", {}), vars)
                await _call_service(hass, service, data)
                step_result["status"] = "ok"
            
            elif step_type == "wait_state":
                entity = _substitute_vars(step["entity_id"], vars)
                success = await _wait_state(
                    hass,
                    entity,
                    in_states=step.get("in"),
                    not_in=step.get("not_in"),
                    attr=step.get("attr"),
                    equals=step.get("equals"),
                    timeout=step.get("timeout", 10)
                )
                step_result["status"] = "ok" if success else "timeout"
            
            elif step_type == "delay":
                seconds = step.get("seconds", 0)
                await asyncio.sleep(seconds)
                step_result["status"] = "ok"
            
            elif step_type == "if":
                # Conditional execution
                condition = step.get("when")
                is_true = bool(vars.get(condition.strip("${}"), False)) if condition else False
                
                if is_true and "then" in step:
                    # Execute then branch (recursively handle nested steps)
                    for sub_step in step["then"]:
                        # Simplified - would need recursive handling
                        if sub_step["type"] == "service_call":
                            service = sub_step["service"]
                            data = _substitute_vars(sub_step.get("data", {}), vars)
                            await _call_service(hass, service, data)
                
                step_result["status"] = "ok"
            
            else:
                step_result["status"] = "unknown_type"
        
        except Exception as e:
            log.error(f"Step {step_result['name']} failed: {e}")
            step_result["status"] = "error"
            step_result["error"] = str(e)
        
        results.append(step_result)
    
    # Determine overall result
    all_ok = all(s["status"] in ("ok", "skipped") for s in results)
    any_error = any(s["status"] == "error" for s in results)
    
    overall = "completed" if all_ok else ("failed" if any_error else "partial")
    
    return {
        "status": "ok",
        "result": overall,
        "steps": results,
        "error": None if all_ok else "Some steps failed"
    }

SPEC = ToolSpec(
    name="run_sequence",
    description=(
        "Execute a sequence of steps (service calls, waits, delays, conditions). "
        "Each step can have guards (only_if_state) and waits ensure previous steps complete before next. "
        "Returns per-step status. Use for complex multi-step operations like media playback."
    ),
    parameters=PARAMS,
    returns="dict with result and per-step statuses",
    func=run_sequence
)
```

### Tool 2: set_preferences with Scene Handling

**File:** `tool_specs/set_preferences.py`

**Key Addition - HA Scene Sync:**

```python
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
    
    # Load existing
    if PREFS_FILE.exists():
        with open(PREFS_FILE) as f:
            prefs = json.load(f)
    else:
        prefs = {"version": 2}
    
    if namespace not in prefs:
        prefs[namespace] = {}
    
    # Merge or replace
    if mode == "merge" and key in prefs[namespace]:
        existing = prefs[namespace][key]
        prefs[namespace][key] = {**existing, **patch}
    else:
        prefs[namespace][key] = patch
    
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
    
    # Save preferences
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
```

### Tool 3: run_sequence Implementation Notes

**Wait Types Supported:**

```python
# 1. wait_state - Wait for entity state change
{
  "type": "wait_state",
  "entity_id": "${plex_client}",
  "not_in": ["unavailable"],
  "timeout": 10  # Max 10 seconds
}

# 2. delay - Fixed time delay
{
  "type": "delay",
  "seconds": 2  # Wait 2 seconds before next step
}

# 3. Implicit waits - service_call with blocking=True
{
  "type": "service_call",
  "service": "media_player.turn_on",
  "data": {"entity_id": "${apple_tv}"}
  // Waits for service to complete before next step
}
```

---

## Implementation Using These Tools

### Save a Scene

**Agent Flow:**
```
1. search_devices(area="gym", domain="light,fan,switch")
2. get_entity_state(all found devices) 
3. confirm_action: "Save current state as gym movie scene?"
4. control_device(
     service="scene.create",
     data={
       "scene_id": "gym_movie",
       "snapshot_entities": ["light.overhead", "light.bias", "fan.ceiling"]
     }
   )
5. set_preferences(
     namespace="scenes",
     key="gym_movie",
     patch={
       "ha_scene_id": "scene.gym_movie",
       "description": "Movie watching",
       "trigger_keywords": ["movie", "film"],
       "areas": ["gym"],
       "device_count": 8
     }
   )
```

**No custom save_scene tool needed!**

### List Scenes

**Agent Flow:**
```
get_preferences(namespace="scenes")
→ Returns all scenes with metadata
```

**Agent formats response:**
```
"You have 3 scenes: gym movie, gym bright, and bedroom cozy"
```

### Activate Scene (Simple)

**Agent Flow:**
```
1. get_preferences(namespace="scenes")  # Get all scenes
2. Find match by keyword (agent does this, not a tool)
3. control_device(
     service="scene.turn_on",
     data={"entity_id": "scene.gym_movie"}
   )
4. set_preferences(
     namespace="scenes",
     key="gym_movie", 
     mode="merge",
     patch={"last_used": "2025-10-02T21:00:00"}
   )
```

### Activate Scene (Complex - with delays/waits)

**Agent Flow:**
```
run_sequence(
  vars={
    "scene_id": "scene.gym_movie",
    "fan_entity": "fan.gym_ceiling"
  },
  sequence={
    "steps": [
      {"type": "service_call", "service": "scene.turn_on", 
       "data": {"entity_id": "${scene_id}"}},
      {"type": "delay", "seconds": 2},
      {"type": "service_call", "service": "fan.turn_on",
       "data": {"entity_id": "${fan_entity}"}}
    ]
  }
)
```

---

## Integration with Screen Playback

**Screen preferences can reference HA scenes:**

```json
{
  "screens": {
    "gym": {
      "apple_tv_entity": "media_player.gym_atv",
      "plex_client_entity": "...",
      "pre_scene": "scene.gym_movie",  // ← Activates HA scene before playback!
      // ... rest of screen wiring
    }
  },
  "scenes": {
    "gym_movie": {
      "ha_scene_id": "scene.gym_movie",
      "trigger_keywords": ["movie"],
      "description": "Dims lights for movie watching"
    }
  }
}
```

**Usage:**
```
User: "Play Moana in gym"
Agent: screen_play(area="gym", rating_key="1234")
  → run_sequence internally:
    1. scene.turn_on(scene.gym_movie)  ← Dims lights
    2. power on Apple TV
    3. select Plex source
    4. scan for client
    5. play media
```

---

## Tools Required

**NO new tools needed!** Use existing:

1. **`get_preferences`** - List/discover scenes
2. **`set_preferences`** - Save scene metadata
3. **`control_device`** - Activate scenes (scene.turn_on, scene.create)
4. **`run_sequence`** - Complex scene activation with timing
5. **`search_devices`** + **`get_entity_state`** - Collect current state

---

## Usage Examples

### Save Scene
```
User: "Save this as gym movie mode"
Agent:
  1. search_devices(area="gym")
  2. get_entity_state(found_devices)
  3. confirm_action
  4. control_device(service="scene.create", data={
       "scene_id": "gym_movie",
       "snapshot_entities": [list of entities]
     })
  5. set_preferences(
       namespace="scenes",
       key="gym_movie",
       patch={"description": "...", "trigger_keywords": ["movie"]}
     )
```

### Activate Scene
```
User: "Set gym to movie mode"
Agent:
  1. get_preferences(namespace="scenes")
  2. Finds "gym_movie" via keyword "movie"
  3. control_device(service="scene.turn_on", data={"entity_id": "scene.gym_movie"})
  4. set_preferences(namespace="scenes", key="gym_movie", 
                     mode="merge", patch={"last_used": "..."})
```

### List Scenes  
```
User: "What scenes do I have?"
Agent:
  1. get_preferences(namespace="scenes")
  2. prepare_voice_response("You have gym movie, gym bright, and bedroom cozy")
```

---

## Agent Decision Logic

**How agent determines what to do:**

```python
# Agent's internal reasoning (from system prompt guidance):

If user says "save this as...":
  → Get current states
  → Create HA scene
  → Save metadata to preferences
  
If user says "set/activate/turn on [scene name]":
  → Load preferences
  → Match keywords
  → Call scene.turn_on
  
If user says "what scenes...":
  → Load preferences
  → List them
  
If user says "play [media] in [room]":
  → Check if screen preferences has pre_scene
  → If yes, activate scene first (within run_sequence)
  → Then continue with media playback
```

---

## Preference Schema

```json
{
  "version": 2,
  "screens": {
    // Screen playback wiring - references scenes via pre_scene
    "gym": {
      "pre_scene": "scene.gym_movie",  // Optional: activate before playback
      "apple_tv_entity": "...",
      // ... other screen wiring
    }
  },
  "scenes": {
    // Scene metadata - lightweight, just keywords/context
    "gym_movie": {
      "ha_scene_id": "scene.gym_movie",
      "description": "Movie watching setup",
      "trigger_keywords": ["movie", "film", "watch"],
      "areas": ["gym"],
      "device_count": 8,
      "created_by": "voice_agent",
      "created_date": "2025-10-02T14:30:00",
      "last_used": "2025-10-02T20:15:00",
      "activation_count": 12
    },
    "gym_bright": {
      "ha_scene_id": "scene.gym_bright",
      "description": "Bright lights for cleaning",
      "trigger_keywords": ["bright", "full", "cleaning"],
      "areas": ["gym"]
    }
  }
}
```

---

## Benefits of Preference-Based Approach

✅ **Fewer tools** - Reuses get/set_preferences  
✅ **Unified storage** - One .special_agent_prefs.json file  
✅ **Integration** - Screens can reference scenes naturally  
✅ **Simpler agent logic** - Less tools to choose from  
✅ **HA scenes still editable** - Preferences just add keywords/context  
✅ **Consistent patterns** - Same API for screens and scenes  

---

## Implementation Checklist

- [ ] Ensure `get_preferences` and `set_preferences` tools exist
- [ ] Ensure `run_sequence` tool exists (for complex activations)
- [ ] Update agent system prompt: guide for scene save/activate flows
- [ ] Test: Save scene → creates HA scene + preferences entry
- [ ] Test: Activate scene by keyword → loads preferences, calls scene.turn_on
- [ ] Test: List scenes → get_preferences(namespace="scenes")
- [ ] Test: Edit scene in HA UI → preferences keywords still work
- [ ] Test: Screen playback with pre_scene → activates scene first

---

## Agent Prompt Guidance

**Add to system prompt:**

```
SCENE MANAGEMENT:
- To save scene: search_devices + get_entity_state → scene.create → set_preferences(namespace="scenes")
- To activate: get_preferences(namespace="scenes") → find by keyword → scene.turn_on
- To list: get_preferences(namespace="scenes") → format list
- Scenes are HA-native (editable in UI), preferences add keywords for discovery
```

---

## Example Session Flows

### Save Scene
```
User: "Save gym lights as movie mode"
Tools called:
  1. search_devices(area="gym", domain="light,fan")
  2. get_entity_state(found_entities)
  3. confirm_action("Save 8 devices as gym movie?")
  4. control_device(service="scene.create", data={...})
  5. set_preferences(namespace="scenes", key="gym_movie", patch={...})
  
Response: "Saved as gym movie mode"
```

### Activate Scene
```
User: "Movie mode in gym"
Tools called:
  1. get_preferences(namespace="scenes")
  2. control_device(service="scene.turn_on", data={"entity_id": "scene.gym_movie"})
  3. set_preferences(namespace="scenes", key="gym_movie", mode="merge", patch={"last_used": "..."})
  
Response: "Gym movie mode activated"
```

### Activate with Media
```
User: "Play Moana in gym"
Tools called:
  1. get_preferences(namespace="screens", key="gym")
  2. search_plex_library("Moana")
  3. run_sequence(
       sequence_ref="play_on_screen.v1",
       vars={
         "pre_scene": "scene.gym_movie",  // From screen preferences
         "rating_key": "1234",
         // ... other vars
       }
     )
     → Internally activates scene first, then plays media
  4. set_preferences(namespace="screens", key="gym", mode="merge", patch={stats...})
  
Response: "Playing Moana in gym"
```

---

## Relationship to Plex Screen Playback

**Scenes integrate seamlessly:**

| Feature | Simple Scene Use | Screen Playback Use |
|---------|-----------------|---------------------|
| Storage | HA scene + preferences | HA scene + screen prefs |
| Activation | control_device(scene.turn_on) | run_sequence with pre_scene |
| Discovery | get_preferences(namespace="scenes") | Read from screen.pre_scene |
| When | "Set lights to movie mode" | "Play movie" (auto-triggers) |

**They complement each other:**
- Scenes work standalone for ambiance
- Screens reference scenes for automated dimming
- Same metadata format, unified storage

---

## Why This is Better

**Compared to original scene-specific tools:**

| Aspect | Original Plan | Preference Plan |
|--------|--------------|-----------------|
| Tools added | 3 new tools | 0 (reuse existing) |
| Storage files | 2 (.special_agent_scenes.json + HA) | 1 (.special_agent_prefs.json + HA) |
| Agent complexity | Learn scene-specific tools | Use familiar get/set pattern |
| Integration | Separate from screens | Natural integration |
| Code to write | ~300 lines | ~20 lines (just prompt guidance) |

**Result:** Simpler implementation, same functionality, better integration!

---

## Implementation Notes

**Minimal changes needed:**

1. **Add to system prompt** - Guidance on scene workflows
2. **Agent learns** - How to use get/set_preferences for scenes
3. **Screen playback** - Already references scenes via `pre_scene`

**No new tool files needed!** Everything uses existing:
- `get_preferences` / `set_preferences` (from Plex plan)
- `control_device` (already exists)
- `run_sequence` (from Plex plan)

---

## Future: Unified Preference Manager

Eventually, one tool could handle all preferences:

```python
manage_preferences(
  action="save|get|list|search",
  namespace="scenes|screens|settings",
  key="...",
  data={...}
)
```

But for now, keeping get/set separate is cleaner and follows the Plex plan pattern.
