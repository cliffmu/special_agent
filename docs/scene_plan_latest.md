# Scene Memory Integration (no legacy "preferences")

> Replace explicit/deterministic "preferences" with **Scene Memory** that learns ordered routines (steps + delays + guards) from voice, UI, and remotes, retrieves **k=1** best strategy at request time, executes, and writes back the outcome for continual improvement.

Key fits with current codebase:

* Tools live in **`tool_specs/`** and are auto-registered by your agent loader.
* Device/entity search already uses **`utils/vector_index.py`**; extend it to also back a **scenes** index without breaking existing device behavior.
* `run_sequence` (or equivalent) executes ordered steps (service calls, waits, delays); keep it as the single executor.
* Remove legacy **preferences** tools once memory is in place.

---

## Implementation Notes & Issue Resolutions

### Configuration Approach

**Only one config setting:** `scene_memory_enabled` (boolean) in `config_flow.py`

**All other parameters hardcoded** in `agent_core.py` for easy tweaking:
```python
SCENE_MEMORY_CONFIG = {
    "retrieval_k": 1,                          # Default scenes to retrieve
    "session_window_seconds": 360,             # Episode grouping (6 min)
    "post_condition_timeout_ms": 4000,         # Per-step verification
    "session_close_idle_seconds": 120,         # Episode flush (2 min)
}
```

**Tool loading based on flag:** Check `scene_memory_enabled` in `agent_core.py` - if enabled, load `get_scene` and `set_scene` tools; if disabled, don't load them (simpler than checking in each tool).

### Timeout Concepts (Issues 9 & 10)

Three distinct timeout concepts:

1. **`session_window_seconds: 360`** (Event Observer)
   - Max gap between device actions to merge into one episode
   - Groups ordered steps (e.g., AVR→wait→HDMI) into single routine
   
2. **`session_close_idle_seconds: 120`** (Scene Memory flush)
   - Idle period before closing episode and running write-backs
   - Captures user corrections after scene execution
   - **Implementation:** Start with (a) immediate write-back only; add (c) session-close flush to backlog
   
3. **`session_timeout_minutes: 5`** (Conversation/LLM)
   - Voice/chat conversation lifetime (existing system)
   - UX only, independent of episode learning

**Post-conditions (`post_condition_timeout_ms: 4000`):**
- Per-step verify window after each service call in `run_sequence`
- Poll entity state/attr until success or timeout (4s default)
- On timeout: mark step as fail, abort sequence, return outcome="fail"

### Vector Index Architecture (Issue 1)

**Key insight:** The existing `query_vector_index()` is already generic - it works on any `(matrix, docs)` tuple. We just need:
1. **Rename** device-specific build/load functions for clarity
2. **Keep** the generic query function as-is
3. **Add** scene-specific wrappers that use the same generic functions with different persist_dir

**No "generic_index" functions needed** - existing functions already work generically when pointed to different directories.

**Directory structure** (persists across component updates):
```
sa_vector_index/              # Outside custom_components, survives updates
├── devices/
│   ├── matrix.npy
│   ├── mapping.json
│   └── meta.json
└── scenes/
    ├── matrix.npy
    ├── mapping.json
    └── meta.json
```

**Approach:**
- Rename `build_vector_index` → `build_device_index` (calls existing code with `persist_dir/devices`)
- Scene index uses same code with `persist_dir/scenes`
- Both use same embedding, caching, query logic
- Start with full rebuild on scene updates; track performance and add incremental upsert to backlog if needed

### Event Observer & Distillation (Issues 2 & 3)

**Observer:**
- Run from day 1 with strict domain filtering (use PREFERRED_DOMAINS from `utils/constants.py`)
- Only capture `"human_ui_or_mobile_app"` and `"our_agent"` causes; ignore automations/sensors
- Manual trigger tool for development/debugging
- Log episode counts before processing

**Distiller:**
- Rule-based initially (no LLM calls)
- Development controls: temp limits, logging, optional HA UI button
- HA service `special_agent.distill_memory` for manual/scheduled execution

### Strategy Injection (Issue 8)

- Use USER message (append to messages list), NOT system prompt
- Simpler to implement
- **BACKLOG:** Monitor if system prompt injection becomes necessary

### Result Write-back Timing (Issue 7)

- **Current approach:** Synchronous `set_scene` before response (simple)
- **BACKLOG:** Async after response if timing shows synchronous is too slow
- Track `set_scene` duration in performance monitoring to verify

### Migration (Issue 6)

- **Hard cutover:** Remove `get_preferences`, `set_preferences`, `generate_scene` early in process
- No migration needed (preferences not actively used)

---

## Phase 0 — Feature flag & hardcoded config

**Goal:** Safe rollout with simple enable/disable.

**Config flow change:**
Add to `config_flow.py` (both `async_step_user` and `async_step_init`):

```python
vol.Optional("scene_memory_enabled", default=False): bool,
```

**Agent core constants:**
Add to `agent_core.py` near top of file:

```python
# Scene Memory configuration (hardcoded for easy tweaking)
SCENE_MEMORY_CONFIG = {
    "retrieval_k": 1,                          # Default scenes to retrieve
    "session_window_seconds": 360,             # Episode grouping window (6 min)
    "post_condition_timeout_ms": 4000,         # Per-step verification timeout
    "session_close_idle_seconds": 120,         # Episode flush after idle (2 min)
}
```

**DoD**

* [ ] Single boolean flag `scene_memory_enabled` in config
* [ ] Hardcoded constants in agent_core.py
* [ ] Easy to tweak values without UI changes

---

## Phase 1 — Remove legacy preferences tools

**Goal:** Clean slate before adding new scene memory tools.

**Remove from agent_core.py base_tools:**
- `"tool_specs.get_preferences"`
- `"tool_specs.set_preferences"`
- `"tool_specs.generate_scene"` (stub)

**Delete files:**
- `tool_specs/get_preferences.py`
- `tool_specs/set_preferences.py`
- `tool_specs/generate_scene.py`

**DoD**

* [ ] No preferences tools loaded
* [ ] Files deleted
* [ ] Agent still works without them

---

## Phase 2 — Rename device index tool & functions

**Goal:** Make it explicit that current index is for HA devices/entities only.

### 2a) Rename in `tool_specs/`

**Rename file:** `build_vector_index.py` → `build_device_index.py`

**Changes in file:**
- Function: `build_vector_index` → `build_device_index`
- SPEC name: `"build_vector_index"` → `"build_device_index"`
- Description: Add "device/entity" clarity
- Imports: Update to use renamed `utils/vector_index.py` functions

### 2b) Rename in `utils/vector_index.py`

**Rename functions for device-specific usage:**
- `build_vector_index` → `build_device_index`
- `load_vector_index` → `load_device_index`  
- `async_load_vector_index` → `async_load_device_index`

**Keep generic functions as-is:**
- `query_vector_index` (already works on any index data)
- `load_vector_meta`, `async_load_vector_meta`
- `_semantic_embed`, `_hash_embed` (internal)

**Update persist_dir default:**
```python
DEFAULT_DEVICE_PERSIST_DIR = os.path.join(DEFAULT_PERSIST_DIR, "devices")
```

All device index functions use `.../sa_vector_index/devices/` subdirectory.

### 2c) Update agent_core.py tool loading

Change base_tools from:
```python
"tool_specs.build_vector_index",
```

To:
```python
"tool_specs.build_device_index",
```

**DoD**

* [ ] Tool renamed to `build_device_index`
* [ ] Functions renamed in vector_index.py
* [ ] Device index uses `.../sa_vector_index/devices/` subdirectory
* [ ] Existing device search still works

---

## Phase 3 — Create scene memory tools (conditionally loaded)

**Goal:** Add get_scene and set_scene tools that are only loaded when enabled.

### 3a) Update agent_core.py tool loading

**Add conditional loading** in `Agent.load_tools()`:

```python
# Scene Memory tools (conditional)
scene_memory_enabled = self.config.get("scene_memory_enabled", False)
if scene_memory_enabled:
    base_tools.extend([
        "tool_specs.get_scene",
        "tool_specs.set_scene",
    ])
    log.info("Scene memory enabled - scene tools loaded")
else:
    log.debug("Scene memory disabled - scene tools not loaded")
```

This way tools don't need to check the flag - they simply won't be available to LLM if disabled.

### 3b) Create `tool_specs/get_scene.py`

**Purpose:** Retrieve the best learned routine.

**Input (JSON schema):**
- `intent: string` — user intent (e.g., "movie", "cozy")
- `area: string?` — optional area/room
- `k: integer = 1` — number of scenes to retrieve (default 1)

**Output:**
- `commands_list: list | null` — ordered steps for `run_sequence`
- `confidence: float` — 0..1
- `strategy_item: {title, description, content} | null` — strategy text for context

**Implementation:**
- Import from `utils.scene_memory_index`
- Call `search_scenes(intent, area, k, hass)`
- Return top result or null if not found
- No need to check enabled flag (tool won't be loaded if disabled)

### 3c) Create `tool_specs/set_scene.py`

**Purpose:** Write back execution outcome for learning.

**Input:**
- `intent: string`
- `area: string?`
- `steps: list` — steps that were executed
- `outcome: "success" | "fail" | "corrected"`
- `notes: string?`

**Output:** `"ok"`

**Implementation:**
- Import from `utils.scene_memory_store` and `utils.scene_memory_index`
- Upsert memory entry
- Rebuild scene index
- **Track timing** with performance monitoring (`tool_set_scene`)

**DoD**

* [ ] Tools only load when `scene_memory_enabled` is true
* [ ] Both tools registered correctly
* [ ] Performance tracking captures `set_scene` duration

---

## Phase 4 — Extend vector_index.py for scenes

**Goal:** Add scene index support using existing generic functions.

**Key insight:** `query_vector_index()` already works on any `(matrix, docs)` tuple. We just need build/load wrappers for scenes that point to different directory.

### Add scene-specific build/load wrappers

**Add to `utils/vector_index.py`:**

```python
DEFAULT_SCENE_PERSIST_DIR = os.path.join(DEFAULT_PERSIST_DIR, "scenes")

def build_scene_index(docs, force_rebuild=False):
    """Build scene index from memory entries."""
    # Reuse existing embed/build logic
    # docs = [{"page_content": str, "metadata": dict}, ...]
    # Save to DEFAULT_SCENE_PERSIST_DIR
    pass

def load_scene_index():
    """Load scene index from disk."""
    # Reuse existing load logic
    # Load from DEFAULT_SCENE_PERSIST_DIR
    pass

async def async_load_scene_index(hass=None):
    """Async load scene index."""
    pass
```

**Existing functions used for both:**
- `_semantic_embed()` - works on any text
- `query_vector_index()` - works on any `(matrix, docs)` tuple
- Internal file I/O helpers

**Directory structure** (persists across updates):
```
sa_vector_index/              # Outside custom_components
├── devices/                  # Device/entity index
│   ├── matrix.npy
│   ├── mapping.json
│   └── meta.json
└── scenes/                   # Scene memory index
    ├── matrix.npy
    ├── mapping.json
    └── meta.json
```

**Performance:**
- Start with full rebuild on scene updates
- Track rebuild time with performance monitoring
- **BACKLOG:** Implement incremental upsert if timing shows it's problematic

**DoD**

* [ ] Can build/query scenes index without affecting device index
* [ ] Both use same embedding/query functions
* [ ] Stored in `.../sa_vector_index/scenes/` subdirectory
* [ ] Performance tracking shows scene index rebuild time

---

## Phase 5 — Scene Memory store & index adapter

**Goal:** Persistence and search for scene memory entries.

### Create `utils/scene_memory_store.py`

**MemoryEntry structure:**
```python
{
    "id": str,              # Unique identifier
    "intent": str,          # Scene intent/name
    "area_hint": str,       # Optional area
    "summary": str,         # Short description
    "steps": list,          # Ordered steps for run_sequence
    "strategy": str,        # How/why strategy text
    "confidence": float,    # 0..1
    "updated_at": str,      # ISO timestamp
}
```

**Methods:**
- `get(id) -> dict | None`
- `upsert(entry_dict) -> None`
- `delete(id) -> None`
- `iter_all() -> Iterator[dict]`

**Storage:** JSON file at `.../sa_vector_index/scene_memory.json` (survives updates)

### Create `utils/scene_memory_index.py`

**Purpose:** Wraps vector index for scene-specific operations.

**Methods:**
- `upsert_scene(entry_dict, hass=None) -> None`
  - Update store
  - Rebuild scene index using `build_scene_index()`
- `search_scenes(intent_text, area=None, k=1, hass=None) -> list[entry_dict]`
  - Load scene index
  - Query with `query_vector_index()`
  - Return matching entries
- `remove_scene(id, hass=None)`
  - Delete from store
  - Rebuild index

**DoD**

* [ ] `get_scene` can retrieve top-1 with steps + strategy + confidence
* [ ] `set_scene` can upsert and rebuild index
* [ ] Storage file persists across component updates

---

## Phase 6 — Strategy injection & LLM rules

**Goal:** Use retrieved strategy in planning.

**Approach:** Append strategy to USER message history (not system prompt).

**Implementation in agent_core.py:**

When `get_scene` returns a result with `strategy_item`, the tool result already contains it. The LLM sees it in the function output and can use it for planning.

**If additional emphasis needed**, after `get_scene` tool result, append:
```python
{
  "role": "user",
  "content": "Relevant Memory:\nTitle: {title}\n{content}"
}
```

**LLM Rule additions to system prompt:**

```
SCENE MEMORY:
- For scene-like requests ("cozy", "movie", "good night"):
  1) Call get_scene(intent, area, k=1) first
  2) Strategy info will appear in the tool result
  3) If commands_list present with confidence >0.6, use it with run_sequence
  4) Otherwise compose steps using device search
  5) After execution, call set_scene with success/fail/corrected outcome
```

**BACKLOG:** Monitor if system prompt injection becomes necessary.

**DoD**

* [ ] Strategy appears in tool result context
* [ ] LLM rules added to system prompt
* [ ] Default k=1 retrieval

---

## Phase 7 — Post-condition verification in run_sequence

**Goal:** Add per-step state verification with timeout.

**Update `tool_specs/run_sequence.py`:**

**Add post-condition support to step execution:**
```python
{
    "type": "service_call",
    "service": "media_player.turn_on",
    "data": {"entity_id": "media_player.avr"},
    "post_condition": {
        "entity_id": "media_player.avr",
        "state": "on",
        "timeout_ms": 4000  # Override default
    }
}
```

**Implementation:**
- After service call, if `post_condition` present, poll entity state
- Use `post_condition_timeout_ms` from SCENE_MEMORY_CONFIG
- If timeout: mark step as fail, abort sequence, return outcome="fail"
- Track in step results

**DoD**

* [ ] Post-condition checks work with configurable timeout
* [ ] Timeouts are logged and result in fail outcome
* [ ] Step results include verification status

---

## Phase 8 — Execution flow & result write-back

**Goal:** Complete execution cycle with learning.

**Typical flow:**
1. `get_scene(intent, area, k=1)`
2. (Optional) `confirm_action` for multi-device routines
3. `run_sequence(sequence={"steps": commands_list})`
   - Include post-condition verification per step
4. Immediately **respond** to user (prepare_voice_response)
5. `set_scene(...)` **write-back:**
   - **Current:** Synchronous call before response (simple)
   - Track duration with performance monitoring
   - **BACKLOG:** If timing shows >500ms, implement async write-back after response

**DoD**

* [ ] Learned routines execute successfully
* [ ] Post-condition checks work
* [ ] Performance tracking shows `set_scene` duration
* [ ] Results written back immediately

---

## Phase 9 — Event Observer (controlled, filtered)

**Goal:** Learn from UI/remote actions.

**Create `utils/event_observer.py`:**

**Features:**
- Listen to `state_changed` and `call_service`
- **Domain filtering:** Only PREFERRED_DOMAINS from `utils/constants.py`
- **Cause classification:**
  - `"human_ui_or_mobile_app"` if `context.user_id` present
  - `"our_agent"` if tagged or traced
  - Ignore automations/sensors
- **Sessionize:** Group by area + time window (session_window_seconds)
- **Order preservation:** Strict ordering + delays

**Development controls:**
- Manual trigger method
- Log episode counts
- Temp limits during development
- Optional HA UI button

**API:**
```python
class EventObserver:
    async def async_setup(hass): ...
    def drain_episodes(self) -> list[dict]: ...
    async def manual_trigger(self): ...
```

**DoD**

* [ ] UI toggles create episodes with correct cause
* [ ] Automations/sensors filtered out
* [ ] Manual trigger for testing
* [ ] Logs show counts before processing

---

## Phase 10 — Nightly Distiller (rule-based, controlled)

**Goal:** Convert episodes to memory entries.

**Create `utils/scene_memory_distill.py`:**

**Approach:** Rule-based (no LLM calls)
- Heuristic pattern matching to merge episodes
- Simple intent labeling (e.g., "movie-like in living_room")
- Generate strategy text from step patterns
- Compute confidence from success/fail + recency

**Execution:**
- HA service `special_agent.distill_memory`
- Manual tool for development
- Optional: scheduler for nightly batch

**Development features:**
- Log episode counts
- Temp limits
- Clear logging of learned scenes

**DoD**

* [ ] Creates/updates MemoryEntries from episodes
* [ ] Calls `scene_memory_index.upsert_scene()` for changed entries
* [ ] Manual trigger for testing
* [ ] Rule-based (no LLM)

---

## Phase 11 — Contracts & Integration

Minimal stable contracts:

- `get_scene(intent, area?, k=1)` → `{commands_list?, confidence, strategy_item?}`
- `set_scene(intent, area?, steps[], outcome, notes?)` → `"ok"`
- `event_observer.drain_episodes()` → `list[episode]`
- `distill(episodes)` → `list[entry_dict or patch]`
- `scene_memory_index.search_scenes(text, area?, k, hass?)` → `list[entry_dict]`

---

## Backlog Items (Verify & Implement If Needed)

**Based on performance tracking data:**

1. **Async result write-back:** If `set_scene` timing is slow (>500ms), implement background update after response
2. **Session-close flush:** Add write-back at session close to capture user corrections
3. **Incremental scene index updates:** If full rebuild is too slow (>1s for <100 scenes)
4. **System prompt injection:** If user message strategy injection doesn't provide enough weight
5. **LLM-based distillation:** If rule-based labeling produces poor results

---

## Performance Tracking Requirements

Track in performance monitoring:

- `tool_build_device_index` (renamed from build_vector_index)
- `scene_index_rebuild` (track scene index rebuild time)
- `tool_set_scene` (track learning write-back time)
- `distill_episodes` (track distillation)
- `event_observer_drain` (track episode collection)

---

## Configuration Summary

**Config flow (UI):**
```python
scene_memory_enabled: bool = False    # Master switch only
```

**Hardcoded in agent_core.py:**
```python
SCENE_MEMORY_CONFIG = {
    "retrieval_k": 1,
    "session_window_seconds": 360,
    "post_condition_timeout_ms": 4000,
    "session_close_idle_seconds": 120,
}
```

**Separate (existing):**
```python
session_timeout_minutes: int = 5      # Conversation lifetime
```

---

## Smoke Tests

1. **Enable scene memory** in HA config UI
2. Use UI: power AVR → wait → TV HDMI2 → dim lights
3. Run `special_agent.distill_memory` service
4. Say "movie time" → agent calls `get_scene`, runs learned routine
5. Verify performance tracking shows timing
6. Change HDMI timing so try fails → agent writes back outcome="fail"
7. Verify scene index rebuild time <1s for <100 scenes
8. **Disable scene memory** → verify scene tools not loaded, agent works normally

---

## Notes for Implementation

- Keep `tool_specs/` and `utils/` structure intact
- Only `scene_memory_enabled` boolean in config UI
- All other config hardcoded in `agent_core.py` for easy tweaking
- Check enabled flag in `agent_core.py` - load scene tools or not
- Existing `query_vector_index()` works for both device and scene indexes
- Just add scene-specific build/load wrappers pointing to `.../scenes/` subdirectory
- Vector indexes stored in `.../sa_vector_index/` (persists across component updates)
- Use PREFERRED_DOMAINS from `utils/constants.py` for filtering
- Track all timing with performance module
- Keep simple: synchronous `set_scene`, full scene index rebuild initially
- Optimize based on performance data, not assumptions
- Remove legacy preferences tools early (Phase 1)
- Core functionality (Phases 1-8) before advanced features (Phases 9-10)
