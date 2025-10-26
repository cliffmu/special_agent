# Scene Execution Tracking & Auto-Save Plan

## Intent

Replace fake "confidence" scores with real execution data. Enable intelligent scene evolution through **hybrid verification**: programmatic execution tracking + agent judgment on intent fulfillment and verification accuracy.

## TL;DR

**What changed from original plan:**
- **Added:** Detailed step results from `run_sequence` (guard evaluations, state snapshots, failure classification)
- **Added:** Agent reviews results to verify user intent AND scene verification accuracy
- **Added:** Agent fixes incorrect verification logic (text mismatches, wrong entities, delays) via `update_scene_ref`
- **Changed:** `set_scene` kept but de-emphasized (most operations via `run_sequence` auto-save + updates)
- **Why:** Programmatic verification is too brittle for edge cases; agent judgment handles fuzzy matching, wrong entities, intent validation

**Workflow:**
1. `run_sequence` executes → Returns detailed results with step-by-step data
2. Agent reviews → Checks if user intent fulfilled + if scene's checks are correct
3. If verification wrong but intent succeeded → Agent fixes guards/delays, runs corrected version with `update_scene_ref`
4. If intent failed → Agent tries different approach
5. Programmatic tracking records all runs (success/failure counts, timestamps)

**Result:** Scenes self-improve over time; false failures decrease; agent learns what "good verification" looks like.

## Problem Statement

### Current Issues:
1. **Fake confidence scores** - Every scene has hardcoded `"confidence": 0.8` with no real data
2. **Agent can't reliably save scenes** - After running `run_sequence`, agent calls `set_scene` but doesn't have the original steps
3. **No learning from failures** - System can't identify flaky scenes or track improvement over time
4. **No template system** - Agent has no examples for new device setups
5. **False-positive scene modifications** - When testing "faster" scenes, guards skip steps because devices already on, resulting in invalid test
6. **Brittle verification logic** - Scenes have incorrect state checks (wrong entity, wrong expected value, missing checks) causing false failures
7. **Programmatic success ≠ user intent** - Steps complete successfully but wrong device/action executed

### Example Failure #1 (from logs):
```
Agent: run_sequence(...) → SUCCESS (5 steps completed)
Agent: set_scene(outcome="success") → ERROR (no steps provided)
Agent tries again with steps in notes → ERROR (notes aren't structured steps)
```

**Root cause:** Agent loses step data between `run_sequence` call and `set_scene` call.

### Example Failure #2 (modification testing):
```
User: "Turn on gym TV and play Plex"
Agent: run_sequence(...) → Success (TV now on, Plex playing)
User: "That was too slow, make it faster"
Agent: Reduces delays, runs modified sequence
Guards: TV already on → Skip power-on steps (2s instead of 15s)
Result: "Success" but didn't test cold start
Agent: Saves broken scene thinking it works
```

**Root cause:** Modified scenes tested with warm state, not cold start.

### Example Failure #3 (verification accuracy):
```
Scene step 5: only_if_state checks app_name == "Plex"
Reality: app_name == "com.plexapp.plex"
Result: Scene fails at step 5 (guard condition not met)
Reality: Plex IS open and working, but text doesn't match exactly
```

**Root cause:** Brittle state checks with exact text matching.

### Example Failure #4 (intent vs execution):
```
User: "Turn on gym TV"
Agent: run_sequence([turn_on media_player.bedroom_tv])
Result: "completed" ✅ (programmatically)
Reality: Wrong device ❌ (user intent not fulfilled)
```

**Root cause:** Programmatic success doesn't verify user intent.

## Goal

1. **Data-driven scene reliability** - Replace confidence with real success/failure counts
2. **Hybrid verification** - Programmatic execution tracking + agent judgment for intent fulfillment
3. **Self-improving verification** - Agent detects and fixes incorrect state checks, delays, entity references
4. **Template system** - Provide example scenes for common AV setups
5. **Flaky scene detection** - Identify scenes that fail frequently
6. **Valid modification testing** - Always test scene changes with cold state (devices off) to ensure accuracy
7. **Minimal agent overhead** - Agent reviews results but doesn't manually track scenes

## Design Decisions

### ✅ What We're Doing:

1. **Execution stats in scene metadata** - Track success/failure counts, timestamps
2. **Detailed step results from `run_sequence`** - Return guard evaluations, state snapshots, verification details
3. **Agent reviews execution results** - Checks if user intent fulfilled AND if scene's verification logic is correct
4. **Agent-guided scene updates** - Fixes incorrect guards, delays, entity checks by running corrected sequence
5. **Origin designation** - Mark scenes as user-created, template, or background-generated
6. **Template scenes** - Read-only examples in `docs/template_scenes.json`
7. **State reset before modification testing** - Turn off devices before testing modified scenes
8. **Smart auto-save** - Don't save trivial single actions, only multi-step workflows
9. **Keep `set_scene` minimal** - Available for explicit scene creation/updates, but most operations via `run_sequence`

### ❌ What We're NOT Doing (Yet):

1. **Separate sequence history vector** - Execution counts in scenes sufficient for now
2. **Background agent** - Defer until we prove templates + auto-tracking aren't enough
3. **Save every execution** - Too noisy, just track aggregate stats

### Why This Approach:

- **Hybrid is best of both worlds** - Programmatic execution is fast/reliable, agent judgment handles edge cases
- **Self-improving scenes** - Verification logic evolves based on real-world feedback
- **Minimal complexity** - Extends existing scene structure, no new vectors
- **Solves the failure mode** - Agent doesn't need to track steps anymore
- **Data-driven** - Real execution data replaces guesswork
- **Iterative** - Can add background agent later if needed
- **Removes false positives** - State reset ensures modifications are properly tested
- **Agent reviews, doesn't micromanage** - Detailed results enable smart decisions without manual tracking
- **No scene pollution** - Smart auto-save prevents trivial actions from cluttering scene memory

## Schema Changes

### Scene Schema V2:

```json
{
  "intent": "play_media_gym",
  "area_hint": "gym",
  
  // NEW FIELDS:
  "origin": "user",  // "user" | "template" | "background"
  "is_template": false,  // true for read-only examples
  
  "execution_stats": {  // REPLACES "confidence"
    "success_count": 15,
    "failure_count": 1,
    "last_success_at": 1761371596.10,
    "last_failure_at": 1760996365.94,
    "first_run_at": 1760421702.54,
    "last_run_at": 1761371596.10
  },
  
  "involved_entities": [  // NEW: Auto-extracted from steps for state reset
    "media_player.gym_atv",
    "remote.gym_remote"
  ],
  
  // EXISTING FIELDS (keep):
  "steps": [...],
  "strategy": "...",
  "client_config": {...},
  "summary": "...",
  "updated_at": 1761371596.10,
  
  // REMOVE:
  // "confidence": 0.8  // Fake data, replaced by execution_stats
}
```

### Migration Strategy:

**Existing scenes:**
- Add `origin: "user"` (assume all existing scenes are user-created)
- Add `is_template: false`
- Initialize `execution_stats` with zeros (no historical data)
- Extract `involved_entities` from steps automatically
- Remove `confidence` field

**New scenes:**
- Created with full schema from start
- Auto-populated by `run_sequence`

## Implementation Stages

### Stage 1: Schema Updates & Migration ✅
**Files:** `utils/scene_memory_store.py`

1. Update `SceneMemoryStore` to handle new fields
2. Add migration function to update existing scenes
3. Validate scene schema on load

**Code changes:**
```python
# In SceneMemoryStore
def _migrate_scene_v2(scene: dict) -> dict:
    """Migrate old scene format to v2."""
    if "execution_stats" not in scene:
        scene["execution_stats"] = {
            "success_count": 0,
            "failure_count": 0,
            "last_success_at": None,
            "last_failure_at": None,
            "first_run_at": scene.get("updated_at"),
            "last_run_at": scene.get("updated_at")
        }
    
    if "origin" not in scene:
        scene["origin"] = "user"
    
    if "is_template" not in scene:
        scene["is_template"] = False
    
    # Extract involved entities from steps
    if "involved_entities" not in scene:
        scene["involved_entities"] = _extract_entities_from_steps(scene.get("steps", []))
    
    # Remove old confidence field
    scene.pop("confidence", None)
    
    return scene


def _extract_entities_from_steps(steps: list) -> list:
    """Extract unique entity IDs from scene steps."""
    entities = set()
    for step in steps:
        if step.get("type") == "service_call":
            data = step.get("data", {})
            entity_id = data.get("entity_id")
            if entity_id:
                entities.add(entity_id)
        # Also check guards
        guard = step.get("only_if_state", {})
        guard_entity = guard.get("entity_id")
        if guard_entity:
            entities.add(guard_entity)
    return sorted(list(entities))
```

### Stage 2: Template Scenes ✅
**Files:** `docs/template_scenes.json` (new), `utils/vector_index.py`

Create 5-6 template scenes for common patterns:
1. `TEMPLATE_apple_tv_plex` - Apple TV + Plex playback
2. `TEMPLATE_roku_plex` - Roku + Plex playback
3. `TEMPLATE_harmony_apple_tv` - Harmony + Apple TV + Plex
4. `TEMPLATE_simple_tv_power` - Basic TV power on/off
5. `TEMPLATE_sonos_audio` - Sonos audio playback
6. `TEMPLATE_generic_media_player` - Generic media player control

**Template structure:**
```json
{
  "intent": "TEMPLATE_apple_tv_plex",
  "origin": "template",
  "is_template": true,
  "area_hint": null,
  "summary": "Apple TV power on → Plex app → play media",
  "steps": [
    {
      "type": "service_call",
      "service": "media_player.turn_on",
      "data": {"entity_id": "${media_player_entity}"},
      "only_if_state": {
        "entity_id": "${media_player_entity}",
        "not_in": ["idle", "playing"]
      }
    },
    {
      "type": "delay",
      "seconds": 6,
      "only_if_state": {
        "entity_id": "${media_player_entity}",
        "not_in": ["idle", "playing"]
      }
    },
    {
      "type": "service_call",
      "service": "media_player.select_source",
      "data": {
        "entity_id": "${media_player_entity}",
        "source": "Plex"
      },
      "only_if_state": {
        "entity_id": "${media_player_entity}",
        "attribute": "app_name",
        "not_equals": "Plex"
      }
    },
    {
      "type": "delay",
      "seconds": 3
    },
    {
      "type": "tool_call",
      "tool": "play_plex_media",
      "args": {
        "rating_key": "${rating_key}",
        "plex_client_entity": "${plex_client_entity}",
        "parent_device_entity": "${media_player_entity}"
      }
    }
  ],
  "strategy": "Template: Apple TV power on with guards, Plex app selection, media playback",
  "execution_stats": {
    "success_count": 0,
    "failure_count": 0,
    "last_success_at": null,
    "last_failure_at": null,
    "first_run_at": null,
    "last_run_at": null
  }
}
```

**Integration:**
- Load templates at startup
- Merge with user scenes in vector index
- Agent can search and adapt templates

### Stage 3A: Enhanced `run_sequence` Results ✅
**Files:** `utils/run_sequence_executor.py`

**Return detailed step-by-step execution results for agent review:**
```python
# In utils/run_sequence_executor.py
async def execute_sequence(...):
    results = {
        "result": "completed" | "failed" | "timeout",
        "steps_completed": 5,
        "total_steps": 5,
        "sequence_ref": sequence_ref,  # If running saved scene
        "step_details": [],  # NEW: Detailed per-step results
        "involved_entities": set(),  # Entities touched during execution
        "failure_type": None,  # "verification_mismatch" | "execution_error" | None
    }
    
    for idx, step in enumerate(steps):
        step_result = {
            "step_num": idx + 1,
            "type": step.get("type"),
            "success": False,
            "guard_evaluated": False,
            "guard_skipped": False,
            "guard_details": None,
            "verification": None,
            "error": None,
            "entity_id": None,
            "state_after": None
        }
        
        # Evaluate guard if present
        guard = step.get("only_if_state")
        if guard:
            entity_id = guard.get("entity_id")
            current_state = await _get_full_state(entity_id, hass)
            guard_passed = await _evaluate_guard(guard, current_state)
            
            step_result["guard_evaluated"] = True
            step_result["guard_skipped"] = not guard_passed
            step_result["guard_details"] = {
                "entity_id": entity_id,
                "condition": guard,
                "actual_state": current_state.get("state"),
                "actual_attributes": current_state.get("attributes", {}),
                "passed": guard_passed
            }
            
            if not guard_passed:
                step_result["success"] = True  # Skipped = success
                results["step_details"].append(step_result)
                continue
        
        # Execute step
        try:
            if step["type"] == "service_call":
                entity_id = step["data"].get("entity_id")
                step_result["entity_id"] = entity_id
                step_result["service"] = step["service"]
                
                await _execute_service_call(step, hass)
                
                # Capture state after execution
                if entity_id:
                    results["involved_entities"].add(entity_id)
                    step_result["state_after"] = await _get_full_state(entity_id, hass)
            
            elif step["type"] == "tool_call":
                tool_result = await _execute_tool(step, hass)
                step_result["tool"] = step["tool"]
                step_result["tool_result"] = tool_result
            
            step_result["success"] = True
            
        except Exception as e:
            step_result["success"] = False
            step_result["error"] = str(e)
            results["result"] = "failed"
            
            # Classify failure type
            if step_result["guard_evaluated"]:
                results["failure_type"] = "verification_mismatch"
            else:
                results["failure_type"] = "execution_error"
            
            results["failed_step"] = step_result
            break
        
        results["step_details"].append(step_result)
    
    results["involved_entities"] = sorted(list(results["involved_entities"]))
    return results
```

**Key additions:**
- `step_details[]`: Per-step success, guards, state snapshots
- `guard_details`: Shows condition, actual state, pass/fail
- `state_after`: Full entity state after service calls
- `involved_entities`: All entities touched (for state reset)
- `failure_type`: Distinguishes verification vs execution errors

---

### Stage 3B: Auto-Tracking Logic ✅
**Files:** `tool_specs/run_sequence.py`, `utils/run_sequence_executor.py`, `utils/scene_memory_store.py`

**Add `save_as_scene` and `update_scene_ref` parameters:**
```python
# In tool_specs/run_sequence.py
PARAMS = {
    "type": "object",
    "properties": {
        "sequence_ref": {...},
        "sequence": {...},
        "vars": {...},
        "timeout": {...},
        "save_as_scene": {
            "type": "boolean",
            "description": "Override auto-save behavior. If null (default), auto-saves multi-step workflows. Set true to force save, false to prevent."
        },
        "update_scene_ref": {
            "type": "string",
            "description": "When running a corrected inline sequence, specify the scene ID to update (e.g., after fixing verification logic)."
        }
    }
}
```

**Auto-tracking logic:**
```python
async def run_sequence(
    sequence: dict | None = None,
    sequence_ref: str | None = None,
    save_as_scene: bool | None = None,
    update_scene_ref: str | None = None,
    ...
):
    # Execute sequence and get detailed results
    result = await execute_sequence(...)
    programmatic_success = result.get("result") == "completed"
    
    # Determine if should save
    if sequence and not sequence_ref:  # Inline sequence
        should_save = save_as_scene if save_as_scene is not None else _should_auto_save(steps)
        
        if should_save:
            # Use update_scene_ref if provided, otherwise generate new ID
            scene_id = update_scene_ref or _generate_scene_id(steps, area_hint)
            
            # NOTE: Auto-save marks as success based on programmatic result
            # Agent should verify intent separately and update if needed
            await _auto_update_scene_stats(
                scene_id=scene_id,
                steps=steps,
                success=programmatic_success,
                area_hint=area_hint,
                hass=hass
            )
    
    # Update stats if referencing existing scene
    elif sequence_ref:
        await _update_existing_scene_stats(
            scene_ref=sequence_ref,
            success=programmatic_success
        )
    
    return result


def _should_auto_save(steps: list) -> bool:
    """Determine if sequence should be saved as scene."""
    if len(steps) <= 1:
        return False  # Don't save trivial single actions
    
    if any(s.get("type") == "delay" for s in steps):
        return True  # Save if has timing logic
    
    if any("only_if_state" in s for s in steps):
        return True  # Save if has conditional logic
    
    return False  # Don't save simple parallel service calls


async def _auto_update_scene_stats(
    scene_id: str,
    steps: list,
    success: bool,
    area_hint: str,
    hass: Any
):
    """Auto-save or update scene execution stats."""
    from ..utils.scene_memory_store import SceneMemoryStore
    
    store = SceneMemoryStore()
    existing = store.get(scene_id)
    
    current_time = time.time()
    
    if existing:
        # Update existing scene stats AND steps (for verification fixes)
        stats = existing.get("execution_stats", {})
        if success:
            stats["success_count"] = stats.get("success_count", 0) + 1
            stats["last_success_at"] = current_time
        else:
            stats["failure_count"] = stats.get("failure_count", 0) + 1
            stats["last_failure_at"] = current_time
        
        stats["last_run_at"] = current_time
        existing["execution_stats"] = stats
        existing["steps"] = steps  # Update steps (may have corrected verification)
        existing["involved_entities"] = _extract_entities_from_steps(steps)
        existing["updated_at"] = current_time
        
        store.set(scene_id, existing)
    else:
        # Create new scene (first run)
        intent = _infer_intent_from_steps(steps, area_hint)
        involved_entities = _extract_entities_from_steps(steps)
        
        new_scene = {
            "id": scene_id,
            "intent": intent,
            "area_hint": area_hint,
            "origin": "user",
            "is_template": False,
            "steps": steps,
            "involved_entities": involved_entities,
            "summary": _generate_summary(steps),
            "strategy": "Auto-saved from successful run_sequence execution",
            "execution_stats": {
                "success_count": 1 if success else 0,
                "failure_count": 0 if success else 1,
                "last_success_at": current_time if success else None,
                "last_failure_at": current_time if not success else None,
                "first_run_at": current_time,
                "last_run_at": current_time
            },
            "updated_at": current_time
        }
        store.set(scene_id, new_scene)
    
    # Rebuild vector index if scene was added/modified
    await async_rebuild_scene_index(hass)
```

**Scene ID generation:**
```python
def _generate_scene_id(steps: list, area_hint: str) -> str:
    """Generate stable scene ID from steps and area."""
    import hashlib
    import json
    
    steps_str = json.dumps(steps, sort_keys=True)
    steps_hash = hashlib.md5(steps_str.encode()).hexdigest()[:8]
    
    if area_hint:
        return f"auto_{area_hint}_{steps_hash}"
    else:
        return f"auto_{steps_hash}"
```

---

### Stage 3C: Agent-Guided Verification Workflow ✅
**Files:** `utils/prompt_builder.py`

**Agent reviews detailed results and fixes verification issues:**

**Prompt guidance to add:**
```
SCENE EXECUTION VERIFICATION:

After run_sequence completes, you receive detailed step results in step_details[].
Your job: Verify TWO things:
1. User intent fulfilled (semantic success)
2. Scene's verification logic is correct (technical accuracy)

STEP 1 - ANALYZE RESULTS:
Review step_details for:
- guard_skipped: Device was already in target state (warm state execution)
- guard_details.passed=false: Verification check failed
- guard_details.actual_state vs condition: Text mismatch (e.g., "com.plexapp.plex" ≠ "Plex")
- state_after: Entity state after service calls

STEP 2 - VERIFY USER INTENT:
Call get_entity_state on critical devices to confirm goal achieved:
- User wanted "Play Plex on gym TV"
  → Check: media_player.gym_atv has app_name containing "plex" (case-insensitive)
  → Check: Playback started (state is "playing" or "buffering")
- User wanted "Turn on gym TV"
  → Check: Device state is not "off" or "standby"

STEP 3 - DETERMINE OUTCOME:

A) INTENT SUCCESS + VERIFICATION GOOD:
   → Scene is solid, programmatic tracking already recorded success
   → Respond to user with success message

B) INTENT SUCCESS + VERIFICATION WRONG:
   → User's goal achieved BUT scene's internal checks are incorrect
   → Fix the verification issue:

   Common fixes:
   1. State text mismatch:
      OLD: only_if_state: {entity_id: "...", attribute: "app_name", equals: "Plex"}
      NEW: only_if_state: {entity_id: "...", attribute: "app_name", contains: "plex"}
   
   2. Wrong entity checked:
      OLD: only_if_state: {entity_id: "media_player.bedroom_tv", ...}
      NEW: only_if_state: {entity_id: "media_player.bedroom_apple_tv", ...}
   
   3. Insufficient delay:
      OLD: {"type": "delay", "seconds": 3}
      NEW: {"type": "delay", "seconds": 6}
   
   4. Missing verification:
      ADD: only_if_state guard to ensure state before critical steps

   → Compose corrected steps
   → Call: run_sequence(sequence={corrected_steps}, update_scene_ref="<scene_id>")
   → If corrected sequence succeeds: Respond to user "Done! I've also fixed the scene."
   → If corrected sequence fails: Respond "Keeping original scene."

C) INTENT FAILED + EXECUTION ERROR:
   → User's goal NOT achieved due to real failure
   → Programmatic tracking already recorded failure
   → Try different approach or ask user for clarification

D) INTENT FAILED + VERIFICATION MISMATCH:
   → Scene's checks are wrong AND intent not fulfilled
   → This is ambiguous - may need to fix verification first, then retry
   → Fix verification as in (B), then re-run to see if intent succeeds

CRITICAL RULES:
- Always check step_details.failure_type to distinguish verification vs execution errors
- If failure_type=="verification_mismatch", likely need to fix scene checks
- If failure_type=="execution_error", likely need different approach
- When updating scenes, use update_scene_ref parameter to preserve scene ID
- Don't update templates (origin=="template" or is_template==true)
```

**Example Agent Workflow:**

```
User: "Play Plex on gym TV"

Agent Call 1: run_sequence(sequence_ref="play_plex_gym")

Result:
{
  "result": "failed",
  "failure_type": "verification_mismatch",
  "step_details": [
    {"step_num": 1, "success": true, "guard_skipped": false},
    {"step_num": 2, "success": true, "guard_skipped": false},
    {"step_num": 3, "success": true, "guard_skipped": false},
    {
      "step_num": 4,
      "success": false,
      "guard_evaluated": true,
      "guard_details": {
        "entity_id": "media_player.gym_atv",
        "condition": {"attribute": "app_name", "equals": "Plex"},
        "actual_state": "idle",
        "actual_attributes": {"app_name": "com.plexapp.plex"},
        "passed": false
      }
    }
  ]
}

Thought: Scene failed at step 4 because app_name check expects exact "Plex" 
but actual value is "com.plexapp.plex". This is a verification logic issue.
Let me verify the app is actually open...

Agent Call 2: get_entity_state("media_player.gym_atv")

Result:
{
  "state": "idle",
  "attributes": {"app_name": "com.plexapp.plex", "source": "Plex"}
}

Thought: Confirmed - Plex IS open (app_name and source both show it).
The scene's verification is too strict. I'll fix the guard to use 'contains'
instead of 'equals' and make it case-insensitive.

Agent Call 3: run_sequence(
  sequence={
    steps: [... steps 1-3 same ..., 
      {
        "type": "service_call",
        "service": "media_player.play_media",
        "data": {...},
        "only_if_state": {
          "entity_id": "media_player.gym_atv",
          "attribute": "app_name",
          "contains_lower": "plex"  // FIXED
        }
      }
    ]
  },
  update_scene_ref="play_plex_gym",
  vars={...}
)

Result:
{
  "result": "completed",
  "steps_completed": 5
}

Agent Response: ✅ "Playing on gym TV. I've also improved the scene's reliability 
by fixing a verification check that was too strict."
```

### Stage 4: Minimize `set_scene` Usage ✅
**Files:** Tool descriptions, `utils/prompt_builder.py`

**Rationale:**
- With auto-tracking and `update_scene_ref`, most scene operations via `run_sequence`
- Keep `set_scene` available for edge cases (explicit intent naming, notes, manual creation)
- Update prompt guidance to strongly prefer `run_sequence` for all scene operations

**Implementation:**

**Option A: Keep `set_scene` but de-emphasize** (RECOMMENDED)
```python
# Update tool_specs/set_scene.py description
SPEC.description = """
Save or update scene with custom intent name or notes.

RARELY NEEDED - run_sequence handles most scene operations automatically:
- run_sequence auto-saves multi-step workflows
- Use update_scene_ref parameter to update existing scenes
- Verification fixes happen via run_sequence with corrected steps

USE set_scene ONLY when:
- User explicitly requests "save this as [custom name]"
- Adding notes/strategy explanation to existing scene
- Creating scene from user's verbal description (not executed yet)

For all other scene operations, use run_sequence.
"""
```

**Option B: Remove from primary agent** (MORE RESTRICTIVE)
```python
# In agent_core.py or tool_registry loading
EXCLUDED_TOOLS_PRIMARY_AGENT = ["set_scene"]

# When loading tools for primary agent:
tools = await load_all_tools(hass, config)
tools = [t for t in tools if t.name not in EXCLUDED_TOOLS_PRIMARY_AGENT]
```

**Recommendation: Start with Option A**
- Less restrictive, allows edge cases
- Agent learns through prompt guidance to prefer `run_sequence`
- Can move to Option B later if agent misuses `set_scene`

**Prompt guidance:**
```
SCENE SAVING PRIORITY:
1. PREFERRED: run_sequence with update_scene_ref (for verification fixes, timing adjustments)
2. PREFERRED: run_sequence auto-save (for new multi-step workflows)
3. RARE: set_scene (only for custom naming or pre-execution scene creation)
```

### Stage 5: Agent Prompt Updates ✅
**Files:** `utils/prompt_builder.py`

**Add to system prompt:**
```
SCENE OPERATIONS:
- run_sequence auto-saves multi-step workflows as scenes
- Single actions (turn on light) won't be saved automatically
- Use update_scene_ref parameter when fixing existing scenes
- set_scene tool rarely needed (only for custom naming or pre-execution creation)

SCENE RELIABILITY:
- Scenes track execution_stats (success/failure counts, timestamps)
- If scene has high failure_count, may need verification fixes
- Templates (origin="template") are examples to adapt, don't modify directly

AFTER run_sequence COMPLETES - REVIEW RESULTS:
You receive detailed step_details[] showing:
- Which guards fired (guard_skipped=true means device already in target state)
- Verification checks (guard_details shows expected vs actual state)
- State snapshots after service calls (state_after)
- Failure classification (failure_type: "verification_mismatch" or "execution_error")

Your job: Verify user intent AND scene verification logic.

CHECK 1 - USER INTENT:
Call get_entity_state on critical devices to confirm goal achieved.
Example: User wanted "Play Plex" → Check app_name attribute contains "plex"

CHECK 2 - VERIFICATION ACCURACY:
Review step_details for verification issues:
- State text mismatch (expects "Plex" but actual is "com.plexapp.plex")
- Wrong entity checked
- Insufficient delays
- Missing guards

If intent succeeded BUT verification failed:
→ Fix scene verification via run_sequence(sequence={corrected}, update_scene_ref="...")
→ Common fixes:
  - Change equals to contains/contains_lower for fuzzy matching
  - Fix entity_id in guards
  - Increase delay durations
  - Add missing only_if_state guards

MODIFYING SCENES:
When user asks to make scene "faster", "slower", or change timing:
1. Identify devices (check scene's involved_entities)
2. Turn off those devices to reset to cold state
3. Wait 3-5 seconds for complete shutdown
4. Run modified sequence with update_scene_ref
5. Review results and respond accordingly

CRITICAL RULES:
- Always reset device state before testing modifications (guards skip on warm state)
- Use failure_type to distinguish verification bugs from real failures
- Don't update templates (origin="template" or is_template=true)
- Programmatic tracking is automatic - you just review and fix verification
```

## Testing Plan

### Manual Testing:

**Basic Auto-Tracking:**
1. **Run existing scene** → Verify stats increment
2. **Run new multi-step inline sequence** → Verify auto-creates scene
3. **Run single-step action** → Verify does NOT save as scene
4. **Scene fails** → Verify failure_count increments
5. **Search templates** → Verify templates appear in search

**Scene Modification:**
6. **User asks to "make it faster"** → Verify agent turns off devices first, then tests
7. **Modified scene succeeds** → Verify stats updated, delays reduced
8. **Modified scene fails** → Verify original scene preserved

**Verification Evolution:**
9. **Scene with text mismatch** → Create scene expecting "Plex", actual is "com.plexapp.plex"
   - Verify agent detects verification_mismatch
   - Verify agent checks entity state confirms intent succeeded
   - Verify agent fixes guard to use contains/contains_lower
   - Verify corrected scene runs successfully
10. **Scene with wrong entity** → Guard checks wrong device
   - Verify agent detects issue from step_details
   - Verify agent corrects entity_id in guard
11. **Scene with insufficient delay** → Steps succeed but barely
   - Verify agent reviews state_after snapshots
   - Verify agent increases delay duration
   - Verify corrected scene more reliable
12. **Intent failure vs verification failure** → Test both scenarios
   - Execution error: Real failure, agent tries different approach
   - Verification mismatch: Agent fixes checks and reruns

### Validation:

**Schema & Data:**
- Check `scene_memory.json` has new fields (origin, execution_stats, involved_entities)
- Check stats update after each run (success_count, failure_count, timestamps)
- Check involved_entities extracted correctly from steps

**Auto-Tracking:**
- Check templates don't get modified (is_template=true scenes remain read-only)
- Check vector index includes templates
- Check single actions don't create scenes
- Check multi-step workflows auto-create scenes

**Verification:**
- Check step_details[] returned with guard evaluations, state snapshots
- Check failure_type classification (verification_mismatch vs execution_error)
- Check agent fixes verification issues and uses update_scene_ref
- Check corrected scenes update in place (same scene ID)

**State Reset:**
- Check involved_entities used for state reset before modification testing
- Check guards work correctly with cold state (no false skips)

## Success Metrics

### Week 1:
- ✅ All existing scenes migrated to v2 schema (with involved_entities)
- ✅ Template scenes available in search
- ✅ Auto-tracking working (stats increment)
- ✅ Smart auto-save prevents scene pollution
- ✅ Enhanced step_details[] returned from run_sequence
- ✅ `set_scene` de-emphasized in prompts (prefer run_sequence)

### Week 2:
- ✅ Agent rarely calls `set_scene` (most operations via run_sequence)
- ✅ New scenes created with full schema
- ✅ Flaky scenes identified (failure_count > 3)
- ✅ State reset working for scene modifications
- ✅ No false-positive modification tests
- ✅ Agent detects verification_mismatch and fixes scenes (at least 2 examples)
- ✅ Verification fixes use update_scene_ref correctly

### Month 1:
- ✅ Reduced manual JSON editing (target: <1x/week)
- ✅ Template adaptation working for new devices
- ✅ Scene reliability improved (success_rate > 90%)
- ✅ Scene pollution eliminated (no trivial single-action scenes)
- ✅ Modification success rate >80% (properly tested)
- ✅ Verification evolution working (scenes self-improve over time)
- ✅ False failure rate <5% (most failures are real, not verification bugs)

## Future Enhancements (Deferred)

### Phase 2: Entity-Based Scene Search
- Add `entities` parameter to `get_scene`
- Return scenes involving specific devices
- Helps agent discover entity purposes

### Phase 3: Background Flow Generation
- Extract "turn off" from "turn on + play" scenes
- Generate hypothesis scenes for testing
- Proactive gap filling

### Phase 4: Device Profiles
- Track entity roles (power, playback, volume)
- Only if entity search + templates aren't enough

## Notes

- **Hybrid approach:** Programmatic execution (fast, reliable) + agent judgment (handles edge cases)
- **Self-improvement:** Scenes evolve their verification logic based on real-world feedback
- **Keep it simple:** Start with execution tracking and verification review, add complexity only if needed
- **Data-driven decisions:** Let real usage data guide future enhancements
- **User friction is the metric:** If manual editing persists, revisit design
- **Templates first:** Validate template approach before building background planner
- **Verification > execution:** Many "failures" are actually verification bugs - agent fixes them over time

