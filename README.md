# Special Agent

This is an early scaffold of the Special Agent Home Assistant integration.

The project is gradually migrating to an agent-based architecture. See
[`migration_to_agent_plan.md`](migration_to_agent_plan.md) for the complete
roadmap.

Install by copying this repository into your `custom_components/special_agent`
folder and restart Home Assistant. Use the `special_agent.reload` service to
reload the integration after making changes.

## Debug logging
Go to Settings \u25b8 Devices & Services \u25b8 Special\u00a0Agent \u25b8 3-dot menu \u25b8 Enable debug logging.
Switch off to return to normal (INFO) logging.

## Vector index utilities
`utils/vector_index.py` includes helpers to build and query a simple NumPy-based
index of Home Assistant entities. `async_load_vector_index` asynchronously loads
the saved index using a background thread (or Home Assistant's executor when
available).

Plex issue fix Close session in Plex on AppleTV. Force close Plex on AppleTV. Open Plex on AppleTV. Skip login/register. Activate "Announce as player". Login again and check "Announce as Player" is active.

---

## Backlog / Future Considerations

### Embeddable Tool Calls in Sequences (Performance Optimization)

**Concept:** Allow `run_sequence` to call tools mid-sequence via `tool_call` step type.

**Potential Benefits:**
- ⚡ **Speed:** Reduce LLM round-trips (1 call instead of 3-4)
  - Example: get_scene + search_plex + run_sequence + play = 4 LLM calls (20-40s)
  - With embedded: get_scene + run_sequence(includes search+play) = 2 calls (10-20s)
  - LLM calls are slowest operation by far (2-10s each)
- 🔄 **Reusability:** Complex tool orchestrations become reusable sequences
- 🧠 **Agent-managed logic:** Agent composes/updates tool flows, not hardcoded

**Implementation Needs:**
- Second tool registry: `can_run_in_sequence` flag (like `can_run_parallel`)
- Sequence-safe tools: get_entity_state, search_plex, search_spotify, plex_companion_play
- NOT safe: ask_user, confirm_action, prepare_voice_response, get_scene (recursive)
- run_sequence needs access to agent tool registry

**Trade-offs:**
- ⚠️ Adds logic to scene JSON (agent writes/updates conditional logic)
- ⚠️ More complex than current pure-data scenes
- ❓ Is agent-managed logic better than dev-managed? (Original concern that led to tool-based refactor)

**Key Question:** Does 50% LLM call reduction justify agent managing execution logic?

**Status:** Deferred until performance profiling shows this is critical bottleneck.