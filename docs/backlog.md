# Special Agent - Feature Backlog

Ideas, enhancements, and future work that need more design or aren't currently prioritized.

---

## Performance & UX Improvements

### Auto-Response After `run_sequence` Success

**Goal**: Eliminate the 3rd LLM call after successful scene execution by pre-generating and auto-returning the success message.

**Current Flow** (3 LLM calls):
```
Call 1: get_scene + search_devices + search_media (parallel)
Call 2: run_sequence(scene_steps)
Call 3: prepare_voice_response("Playing Star Trek in the gym!")
```

**Target Flow** (2 LLM calls):
```
Call 1: get_scene + search_devices + search_media + prepare_voice_response (parallel, optimistic)
Call 2: run_sequence(scene_steps) → auto-return pre-generated response if success
```

**Proposed Implementation Options**:

**Option A: `run_sequence` explicit parameter**
```python
run_sequence(
    sequence=[...],
    vars={...},
    success_response="Playing Star Trek in the gym!",  # NEW - pre-generated
    failure_response="Couldn't start playback"         # NEW - optional
)
```
- `run_sequence` returns `{"status": "success", "auto_speak": success_response}` on success
- `agent_core` detects `auto_speak` and returns immediately without Call 3

**Option B: Agent convention (less intrusive)**
- Agent calls `prepare_voice_response` first (stores optimistic message as `pending`)
- Agent calls `run_sequence` 
- If `run_sequence` succeeds AND `pending` exists with `kind="success_message"`, `agent_core` auto-returns `pending["speak"]`
- No parameter changes to `run_sequence`

**Option C: New composite tool**
```python
run_sequence_and_respond(
    sequence=[...],
    vars={...},
    success_message="...",
    failure_message="..."
)
```
- Wraps `run_sequence` + auto-response logic
- Most explicit, but adds tool complexity

**Complexity & Dependencies**:

1. **Scene verification must be reliable**
   - Every critical step needs `post_condition` or `only_if_state` checks
   - Delays must be sufficient for entity state updates (avoid false negatives)
   - Current scenes may not have comprehensive verification

2. **Optimistic message generation**
   - Agent must generate success message *before* knowing if scene will work
   - Requires confidence in scene reliability
   - May need fallback if scene fails (abort auto-return)

3. **Scene structure becomes more rigid**
   - Scenes must have predictable success/failure paths
   - Can't have "partial success" - either all steps work or it fails
   - May need scene versioning/confidence tracking

**Related Requirements**:
- Scenes need robust `post_condition` validation (currently optional)
- Need scene reliability metrics (success rate per scene)
- May need scene testing/validation before enabling auto-response

**Estimated Effort**:
- Option A: 3-4 hours (modify `run_sequence`, add agent_core logic, test)
- Option B: 1-2 hours (add agent_core convention, test)
- Option C: 4-5 hours (new tool, validation, test)
- Scene hardening (verification): 2-3 hours per scene family

**Priority**: Medium (nice-to-have optimization, not blocking core functionality)

**Status**: Design discussion - needs more thought on scene reliability guarantees

---

## Session Management

### Smart `pending` Cleanup

**Status**: ✅ Implemented (session_helpers.py)
- Clear stale `pending` responses when user provides new prompt
- Prevents old questions/responses from leaking into new conversations

---

## Architecture & Code Quality

### Remove `pending` from Session Storage

**Goal**: Simplify session state by removing vestigial `pending` field.

**Context**: 
- `pending` is used to return responses from current turn (via `prompt_payload`)
- But storing it in session is unnecessary since it's cleared on next turn
- The field exists in `Session` dataclass but provides no value after the response is returned

**Options**:
1. **Keep current fix** (clear on load) - minimal, safe, works
2. **Remove from storage** - bigger refactor, breaks existing sessions

**Decision**: Keep Option 1 (current fix). Low value for the effort.

**Status**: Closed - current solution is sufficient

---

## Tool Improvements

### Scene Search Guidance

**Goal**: Ensure agent searches for scenes in Call-1 parallel with other lookups.

**Status**: In progress - updating tool prompts

**Changes**:
- `get_scene`: Emphasize parallel scene search in Call-1
- `set_scene`: Clarify when to save (multi-step, vibe-based, ambiguous entities)
- General guidance: When to use scenes vs direct control

---

## Future Features

### Session Continuation Detection

**Ideas explored**:
- LLM-based topic change detection (rejected: adds latency)
- Cross-device session switching (low priority for current use case)
- Session summarization for long conversations (nice-to-have)

**Status**: Backlog - not currently needed

---

## Notes

- This file captures ideas that need more design discussion or aren't currently prioritized
- Items move to `docs/scene_plan_latest.md` when they become active work
- Keep descriptions focused on "what" and "why", not implementation details

