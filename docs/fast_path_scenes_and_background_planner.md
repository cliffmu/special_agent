Here’s a drop-in **`FAST_PATH_SCENES_AND_BACKGROUND_PLANNER.md`** you can save in your repo.

---

# Fast-Path Scenes & Background Planner — Implementation Plan

**Goal:** Cut voice → action time to **2 LLM calls** using **cached scenes** + `run_sequence` verification. Prepare the codebase so a **background planner** can safely pre-build scene hypotheses later.

---

## 1) Current state (what’s already in repo)

* **Tools + registry refactor**: tools are loaded via a central registry; base tools include `run_sequence`, `get_scene`, `set_scene` when Scene Memory is enabled.  
* **Scene plan**: scenes are retrieved (`get_scene`), executed with `run_sequence`, then written back via `set_scene`, with per-step **post-conditions** and timeouts designed into the plan.   
* **Vector index split**: device vs scene indices persist under `.../sa_vector_index/{devices,scenes}`. 
* **Agent perf hooks**: LLM calls are wrapped with `performance.track_operation`. 
* **Design guardrails**: tool-specific guidance stays in **each tool**, not the global prompt (keeps system prompt stable). 

---

## 2) Scene JSON contract (finalize now)

**Allowed step types:** `service_call`, `delay`, `tool_call`. Scenes must be **deterministic**; keep searches out of scenes. **Only** tools with `can_run_in_sequence=True` may be called from a `tool_call`.  

**Per-step guards & verification:**

* `only_if_state` to skip steps cheaply.
* `post_condition` to assert target state/attr with timeout; abort on failure. 

**Variable binding:** `${var_name}` placeholders in `args` (e.g., `rating_key`, `plex_client`, `parent_device_entity`); bound at execution. (Pattern already used in docs/examples.) 

**Client config (room defaults):** store scene-local defaults (e.g., **`audio_entity`**, `parent_device_entity`) in `client_config` to avoid repeated disambiguation. You already persist per-scene client config; extend with `audio_entity`. 

**Minimal MemoryEntry** (additions in **bold**):

```json
{
  "id": "string",
  "intent": "string",
  "area_hint": "string",
  "summary": "string",
  "steps": [ ... ],
  "strategy": "string",
  "confidence": 0.0,
  "updated_at": "iso-datetime",
  "client_config": { "audio_entity": "media_player.x", "...": "..." },  // NEW
  "off_scene_id": "power_off_<room>",                                   // NEW
  "origin": "manual|background",                                        // NEW
  "tested": false                                                       // NEW
}
```

(Base structure per plan; extend safely.) 

---

## 3) Fast-path execution (2 LLM calls)

1. **Planner turn**: parse request, then parallel **read-only** lookups (exact scene `k=1`, similar scenes, device/media hits). 
2. **Executor turn**: bind vars; call `run_sequence(sequence, vars)` once; rely on post-conditions; then `set_scene` write-back. 

> Keep the prompt hygiene: global prompt stays generic; tool behaviors live in the tool specs. 

---

## 4) What to do **next** (now) to harden speed path

**P0 — correctness & determinism**

* **Allowlist** only deterministic tools for `tool_call` (`play_plex_media`, `get_entity_state/history`); **disallow** `search_*` tools from scenes. Enforce in validator. 
* **Unify timeout return shape** in executor: return `{result: "failed", steps, total_steps, completed_steps, error}` on any timeout. (Keeps callers simple.)
* **Tool-step equality** in scene upsert: treat two steps as equal only if `tool`, `args`, `result_var`, `result_path` all match (prevents “no-op” updates when args change).
* **Require `hass` for guards**: if a step has `only_if_state`/`post_condition` and `hass` is `None`, **fail fast** (clear error).

**P1 — scene structure for ON/OFF & volume**

* Create **paired OFF scenes** per room (`power_off_<room>`), and set `off_scene_id` in the ON scene’s metadata. (Simple linkage beats toggle logic.)
* Add **`audio_entity`** to each media scene’s `client_config`. On the first disambiguation, persist the choice for that room.

**P1 — retrieval parallelism**

* In **Call-1**, fetch **exact** (`intent+area, k=1`) **and similar** scenes (`intent, k=3`) in **parallel**; also parallel device/media lookups. Plan already assumes k=1 default and shows similar reuse. 

**P1 — perf clarity**

* Wrap every `run_sequence` step with perf timing; keep per-step durations for future delay optimization (you already wrap LLM calls). 

**P1 — docs & contracts**

* Update `docs/scene_plan_latest.md` with the **final `tool_call` schema + allowlist rule** and ON/OFF pairing fields (add `off_scene_id`, `audio_entity`). Base doc already outlines `tool_call` & expectations. 

---

## 5) Background planner (deferred; design now)

**Inputs:** HA history (recent days), your **Event Observer** (episodes of human-initiated actions), device inventory. (Observer & Nightly Distiller are already specced.)  

**Process:** nightly job → mine routines → produce **scene hypotheses** (untested) with `origin="background"`, `tested=false`, `confidence`. 

**Outputs:**

* **ON**, **OFF**, **volume default**, **app-launch** scenes per room (disabled for auto-run).
* Optional **automation suggestions**: HA automation (disabled) whose action calls `special_agent.run_sequence` or a compiled HA script.

**Suggestion UX:** “Enable / Try once / Snooze / Never”; *Try once* runs the script now and uses `post_condition` to verify. (Runtime path remains 2-call for voice; zero-LLM for HA triggers.)

**Guardrails:** limit to safe domains (lights/media/switch), quiet hours, cooldowns, conflict checks; keep **explainability** (“seen N× at 9-11 pm”). (All in the Events/Distiller plan.)

---

## 6) HA Automations integration (optional later)

* **Recommended wiring:** HA **trigger** → action = `special_agent.run_sequence` with `{scene_id, vars}` for zero-LLM latency on entity triggers; manage in HA UI.
* **Testing:** automation calls a **script**; agent can run the script directly to test and check `post_condition` results before enabling.

---

## 7) Risks & mitigations

* **Nondeterminism in scenes**: forbid `search_*` in saved scenes; do searching in Call-1 and bind concrete vars. 
* **ON/OFF confusion**: use explicit **paired scenes** per room; keep toggle logic out of LLM reasoning.
* **Conflicts (entity thrash)**: later add per-room **mutex** & cooldowns for automations; executor should be idempotent via guards/post-conditions. 
* **Prompt drift**: keep tool rules in tool specs; global prompt stays lean. 

---

## 8) Implementation roadmap

### Phase A — **Ship speed path (now)**

* [ ] Validator: enforce `tool_call` allowlist; **reject** `search_*` tools in scenes. 
* [ ] Executor: **unify timeout** return; require `hass` for guards; per-step timing; consistent `result` field.
* [ ] Scene upsert: **tool-step equality** compares `tool`, `args`, `result_var`, `result_path`.
* [ ] Scenes: add **`audio_entity`** to `client_config`; create **`power_off_<room>`** scenes + `off_scene_id` link.
* [ ] Planner: Call-1 **parallel** fetch exact + similar scenes; device/media lookups; Call-2 bind + execute + write-back.

### Phase B — **Polish & docs**

* [ ] Update `docs/scene_plan_latest.md` with final JSON contract (`tool_call`, `post_condition`), ON/OFF pairing, `audio_entity`.  
* [ ] Add brief “Fast-Path (2 calls)” section to AGENTS.md; keep tool-specific prompts inside tools. 

### Phase C — **Background planner skeleton (no runtime impact)**

* [ ] Implement **Event Observer** (`utils/event_observer.py`) + drain API. 
* [ ] Implement **Nightly Distiller** (`utils/scene_memory_distill.py`) to generate **untested** MemoryEntries with confidence. 
* [ ] Add **origin/tested/confidence** fields to MemoryEntry; ensure `get_scene` can filter or rank by them.

### Phase D — **Suggestion pipeline (opt-in)**

* [ ] Builder: emit **disabled** HA automations whose action calls `run_sequence` or compiled script.
* [ ] “Try once” flow + verification; enable on success.
* [ ] Add limits (top-N per room, safe domains).

---

## 9) Open decisions (make once, stick to them)

* **Action backend for HA automations:** use `special_agent.run_sequence` vs compiled HA script.
* **Confidence thresholds** for “Try once” vs “Enable” defaults for background scenes.
* **Scene storage of defaults:** per-scene `client_config` vs room-level registry (start per-scene).
* **Metrics to track:** P50/P90 end-to-end, first-try success %, clarifications per request, step timings.

---

## 10) Example snippets

**`tool_call` step in scene:**

```json
{
  "type": "tool_call",
  "tool": "play_plex_media",
  "args": {
    "rating_key": "${rating_key}",
    "plex_client_entity": "${plex_client}",
    "parent_device_entity": "${parent_device_entity}"
  },
  "post_condition": {
    "entity_id": "${plex_client}",
    "state": "playing",
    "timeout_ms": 5000
  }
}
```

(Format per plan; allowlisted tools only.) 

**`service_call` with post-condition:**

```json
{
  "type": "service_call",
  "service": "media_player.turn_on",
  "data": { "entity_id": "media_player.avr" },
  "post_condition": { "entity_id": "media_player.avr", "state": "on", "timeout_ms": 4000 }
}
```



---

### Bottom line

Ship **Phase A/B** to lock the 2-call fast path for pre-setup scenes. Once that’s solid, the background planner will **drop in cleanly** (it just fills the same scene format and flags entries as untested until first real run).
