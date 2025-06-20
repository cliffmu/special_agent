# Special Agent — Agent‑Based Migration Plan (v5.6, **self‑contained**)

*This file supersedes every earlier draft (v4.0 → v5.5).
It preserves everything in **v4.0** and **v5.3**, retains the deeper tool specs from the v5.x branch, **adds the `learn_preferences` tool**, and now extends the architecture with **multi‑device / multi‑session handling** plus a **unified user‑prompt flow** (`prompt_user` with thin `confirm_action` & `ask_user` wrappers).*

---

## 0  Project goals & use‑cases (unchanged)

| ID  | Goal                | Typical voice request                              | Outcome                                        |
| --- | ------------------- | -------------------------------------------------- | ---------------------------------------------- |
| G‑1 | Dynamic room scenes | “Make the living‑room cozy.”                       | Dim lights to personal %; fireplace + playlist |
| G‑2 | Whole‑home scenes   | “Good night.”                                      | House shut‑down across all areas               |
| G‑3 | Preference learning | “Set cozy brightness to 8 %.” / implicit fine‑tune | Stored; reused next time                       |
| G‑4 | Info queries        | “Weather tomorrow?”                                | Spoken forecast                                |
| G‑5 | One‑shot controls   | “Turn on the kitchen light.”                       | Confirmation → action                          |

---

## 1  Repository layout (HACS‑compatible)

```text
ROOT/
├─ hacs.json                          # "content_in_root": true
├─ manifest.json
├─ README.md
├─ __init__.py
├─ conversation.py                    # ConversationEntity subclass
│
├─ agent_core.py                      # Plan/Execute loop
├─ session_store.py                   # ★ NEW – multi‑device session & focus memory
│
├─ tool_specs/
│  ├─ control_device.py
│  ├─ search_devices.py
│  ├─ generate_scene.py
│  ├─ preference_manager.py
│  ├─ prompt_user.py                  # ★ NEW – core prompt plumbing
│  ├─ confirm_action.py               # thin wrapper → prompt_user(kind="confirm")
│  ├─ ask_user.py                     # thin wrapper → prompt_user(kind="clarify")
│  ├─ get_weather.py
│  ├─ search_spotify.py
│  ├─ learn_preferences.py
│  ├─ area_iterator.py
│  └─ build_vector_index.py
│
├─ utils/
│  ├─ openai_client.py
│  ├─ vector_index.py
│  ├─ data_sources.py
│  ├─ logging.py                      # QueueHandler/QueueListener pattern
│  └─ constants.py
│
├─ tests/                             # pytest‑homeassistant‑custom‑component
│  ├─ test_preferences.py
│  ├─ test_scene.py
│  ├─ test_multi_session.py
│  └─ ...
│
├─ REFERENCE/
└─ migration_to_agent_plan.md         # this file
```

---

## 2  Coding & naming conventions (unchanged)

| Artifact          | Convention                            | Example                                                      |
| ----------------- | ------------------------------------- | ------------------------------------------------------------ |
| Modules           | `snake_case.py`                       | `search_devices.py`                                          |
| Classes           | `PascalCase`                          | `PreferenceManager`                                          |
| Tool files        | optional `tool_<verb>.py`             | `tool_control_device.py`                                     |
| Voluptuous schema | defined in each `ToolSpec.parameters` | `brightness_pct: vol.All(vol.Coerce(int), vol.Range(0,100))` |
| Tests             | `tests/test_<module>.py`              | `test_scene.py`                                              |

---

## 3  Architecture summary

### 3.1 Agent & tool schema  *(unchanged)*

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: vol.Schema
    returns: str | None
    func: Callable[..., Awaitable]
```

The agent runs a *Plan → Execute* ReAct loop.

### 3.2 Cost‑aware model routing  *(unchanged)*

Heuristic chooses o3‑mini vs o3‑pro based on prompt length (≈ 1 k tokens threshold).
o3‑pro pricing: **\$20 /M in**, **\$80 /M out** vs **\$2 / \$8** for o3‑mini.

### 3.3 Entity retrieval & vector indexes  *(v5.3 additions retained)*

* **v2 metadata enrichment** – every vector stores `domain`, `area_id`, `friendly_name`; build writes `meta.json`.
* **v3 semantic embeddings** – hash buckets → OpenAI *text‑embedding‑3‑small* (1536‑d) with local MiniLM fallback.
* **v3 filtered & hybrid retrieval** – `query_vector_index()` supports metadata masks and a lexical tie‑breaker for better *precision‑at‑k*.

### 3.4 Session manager & focus memory  *(NEW in v5.5)*

`SessionManager` (in `session_store.py`) persists sessions under **(conversation\_id, device\_id)** and stores:

```python
{
  "messages": [...],        # full ReAct trace
  "pending":  {...}|None,  # waiting confirm / clarify payload
  "focus":    {...}|None,  # last successful control (targets + action)
  "device_id": "sat_kitchen",
  "updated":  1718850000.0
}
```

Focus expires after *N* minutes (default = 5), enabling follow‑ups like “a little brighter” without restating the entity.

---

## 4  Tool catalogue (ranked)

| Rank | Tool                         | Inputs                         | Internal operations                                                                                                              | LLM calls            | Returns                 |
| ---- | ---------------------------- | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- | -------------------- | ----------------------- |
| 1    | `control_device`             | `service, data`                | `hass.services.async_call`                                                                                                       | none                 | `"OK"` / error          |
| 2    | `prompt_user`                | `prompt, kind, pending?`       | Central prompt plumbing                                                                                                          | none                 | `{"speak": …}`          |
|  2a  | `confirm_action`             | `action, targets, question`    | Wrapper → `prompt_user(kind="confirm")`; uses **tiny LLM** (< 50 tok) for question wording                                       | none                 | `{"speak": …, pending}` |
|  2b  | `ask_user`                   | `question`                     | Wrapper → `prompt_user(kind="clarify")`                                                                                          | none                 | `{"speak": …}`          |
| 3    | `search_devices`             | `query, area?, domain?, k`     | Filtered cosine search (metadata mask ➜ top‑k, lexical boost)                                                                    | none                 | `[entity_id]`           |
| 4    | **`generate_scene`**         | `intent, area`                 | (a) fetch prefs; (b) 3 × `search_devices` (lights, media\_player, extras); (c) compose command list; (d) optional `scene.create` | none                 | `command_list`          |
| 5    | **`area_iterator`**          | `intent`                       | Loops HA area registry, calls `generate_scene`; dedup; merge                                                                     | none                 | big `command_list`      |
| 6    | `preference_manager`         | `user, area, key, mode`        | JSON store (read / merge / replace)                                                                                              | none                 | value                   |
| 7    | **`learn_preferences`**      | `area, entity_id, prefs, mode` | merge / replace prefs in JSON                                                                                                    | none                 | status                  |
| 8    | `build_vector_index`         | `{force?: bool}`               | Dump HA states → embed → save `.npy`, `mapping.json`, `meta.json`                                                                | none                 | `"rebuilt"`             |
| 9    | `get_weather`                | `location?`                    | Sensor + external API → template answer                                                                                          | **mini** (< 100 tok) | forecast                |
| 10   | `search_spotify`             | `query, type`                  | Spotify REST `/search`                                                                                                           | none                 | URI                     |
| 11   | *(future)* `calendar_lookup` | `date`                         | Google / HA Calendar API                                                                                                         | mini                 | events                  |
| 12   | *(future)* `energy_report`   | `span`                         | HA statistics API                                                                                                                | none                 | stats                   |
| 13   | *(future)* `diagnostic_tool` | `entity_id`                    | HA states → uptime / last‑changed                                                                                                | none                 | health JSON             |

*Wrapper rationale:* explicit tool names keep the LLM’s intent transparent while sharing one implementation path (*prompt\_user*).

\### 4.1 `learn_preferences` design (v5.3 recap)

**Use‑case A – session auto‑save**
When several brightness / colour tweaks hit the same light within 5 min, the final state is saved by calling:

```json
{
  "tool": "learn_preferences",
  "params": {
    "area": "living_room",
    "entity_id": "light.lamp",
    "prefs": { "brightness_pct": 12 },
    "mode": "merge"
  }
}
```

**Use‑case B – explicit user command**
“Remember current settings for the office as ‘focus’ scene.”
Agent: ask clarification → map to scene intent → call `learn_preferences` with `mode:"replace"`.

---

\## 5  Prompt essentials (system)

```
You are Special Agent, a smart‑home AI.
TOOLS:
{{tool_schema}}
RULES:
- If request implies "cozy", "movie", "good night", call generate_scene.
- After a fine‑tune session OR explicit request, call learn_preferences.
- For state changes ALWAYS confirm_action before control_device.
- When you call confirm_action you MUST include a `question` field containing the exact sentence to speak to the user.
- Exclude *_led, *.bass_*, *.treble_* entities.
- Output ONLY valid JSON.
```

---

\## 6  Implementation roadmap & smoke‑tests

| **Phase** | New deliverable                                              | Key tests / exit criteria                  | Status      |
| --------- | ------------------------------------------------------------ | ------------------------------------------ | ----------- |
| **0**     | Repository bootstrap                                         | HACS loads component                       | Done        |
| **1**     | Agent skeleton (no tools)                                    | “Hi” → “can’t help yet”                    | Done        |
| **1b**    | `utils/vector_index.py` utilities                            | `.npy` file exists                         | Done        |
| **2** ★   | **Minimal ReAct loop** + wrappers for existing utilities (`build_vector_index`, stub `search_devices`) | “Rebuild the database.” → tool call returns `"rebuilt"`                                                                              | Done        |
| **2b**    | Nightly cron for `build_vector_index`                        | CLI completes < 30 s                        |
| **2c**    | Metadata enrichment migration                                | `area_id` non‑null; old mapping upgraded   |
| **2d**    | **Filtered `search_devices`**                                | “office light” → correct entity            | Done        |
| **2e**    | **Embedding upgrade + hybrid scorer**                        | similarity tests pass                      |
| **3**     | **Control MVP**: `prompt_user` + wrappers + `control_device` | “Turn on kitchen light” → confirm → call   | Done        |
| **3b**    | Info tools (`get_weather`, `search_spotify`)                 | “Weather?” → spoken response               |
| **3c**    | Harden tool schemas (real JSON export)                       | Arrays & nested dicts validated            |
| **3d**    | **Multi‑device SessionManager**                              | Two satellites hold independent threads    | Done        |
| **4**     | Clarification loop via `prompt_user(kind="clarify")`         | Ambiguous request triggers follow‑up       | Done        |
| **4b**    | `preference_manager`                                         | Recall 8 % brightness                       |
| **4c**    | `generate_scene` + `area_iterator`                           | “Good night” scene                         |
| **5**     | Safety & dedupe (3‑iteration cap)                            | Two speakers, no loop                      |
| **6**     | Test harness (`pytest‑homeassistant`)                        | All tests pass                             |
| **7**     | Docs & contributor guide                                     | README covers tool API                     |
| **8**     | Advanced tools (calendar, energy, diagnostics)               | Drop‑in ToolSpecs                          |
| **9**     | R\&D: proactive automations                                  | Shadow mode only                           |

---

\## 7  Expanded test matrix

| ID   | Scenario                                                                | Expected                         |
| ---- | ----------------------------------------------------------------------- | -------------------------------- |
|  T‑1 | Cozy preference recall                                                  | uses stored 8 %                  |
|  T‑2 | LED exclusion                                                           | leaves `_led` entities untouched |
|  T‑3 | Whole‑home off                                                          | plan ≤ 2 k tokens                |
|  T‑4 | Router heuristic                                                        | long prompt → o3‑pro             |
|  T‑5 | Fine‑tune session auto‑save                                             | last tweak persisted             |
|  T‑6 | Explicit remember command                                               | prefs saved in “replace” mode    |
|  T‑7 | ReAct depth limit                                                       | aborts after 3 rounds            |
|  T‑8 | **Simultaneous rooms** – Kitchen & Bedroom sessions don’t clash         |                                  |
|  T‑9 | **Confirm question param** – LLM includes `question`; validation passes |                                  |

---

\## 8  Persisted data formats

```jsonc
// config/.special_agent_prefs.json
{
  "user_abc": {
    "living_room": {
      "cozy_brightness": 12,
      "cozy_playlist_uri": "spotify:playlist:123"
    }
  }
}
```

```jsonc
// vector_index/living_room/index_meta.json
{
  "area_id": "living_room",
  "created": "2025‑06‑13T10:00Z",
  "entity_count": 185
}
```

```jsonc
// config/.special_agent_sessions.json  (managed by SessionManager)
{
  "abc123|sat_kitchen": {
    "messages": [ /* OpenAI messages */ ],
    "pending":  { "action": "turn_off", "targets": ["light.kitchen"] },
    "focus":    { "targets": ["light.kitchen"], "action": "turn_off" },
    "device_id": "sat_kitchen",
    "updated":  "2025‑06‑18T07:00:12Z"
  }
}
```

---

\## 9  OpenAI usage & cost estimate  *(unchanged)*

| Model       | Typical prompt   | Avg tokens | Cost / req |
| ----------- | ---------------- | ---------- | ---------- |
| **o3‑mini** | one‑shot control | \~ 850     | \$0.001    |
| **o3‑pro**  | large scene      | \~ 4 k     | \$0.015    |

Routing keeps monthly cost ≈ **\$4** for *500 control + 100 scene* requests.

---

\## 10  References

1. HACS “content\_in\_root” – [https://www.hacs.xyz/docs/publish/integration/](https://www.hacs.xyz/docs/publish/integration/)
2. Home Assistant Area registry – [https://developers.home-assistant.io/docs/area\_registry\_index/](https://developers.home-assistant.io/docs/area_registry_index/)
3. ConversationEntity dev guide – [https://developers.home-assistant.io/docs/core/entity/conversation/](https://developers.home-assistant.io/docs/core/entity/conversation/)
4. Recorder integration (history API) – [https://www.home-assistant.io/integrations/recorder/](https://www.home-assistant.io/integrations/recorder/)
5. OpenAI structured outputs – [https://platform.openai.com/docs/api-reference/responses/create](https://platform.openai.com/docs/api-reference/responses/create)
6. OpenAI o3‑pro pricing – [https://community.openai.com/t/o3-is-80-cheaper-and-introducing-o3-pro/1284925](https://community.openai.com/t/o3-is-80-cheaper-and-introducing-o3-pro/1284925)
7. Metadata filtering guide – [https://medium.com/@dmitri.mahayana/ultimate-semantics-search-part-2-metadata-filtering-05cad97bc5da](https://medium.com/@dmitri.mahayana/ultimate-semantics-search-part-2-metadata-filtering-05cad97bc5da)
8. Spotify Web API `search` – [https://developer.spotify.com/documentation/web-api/reference/search](https://developer.spotify.com/documentation/web-api/reference/search)
9. Home Assistant scenes – [https://www.home-assistant.io/docs/scene/](https://www.home-assistant.io/docs/scene/)
10. Voluptuous validation – [https://pypi.org/project/voluptuous/](https://pypi.org/project/voluptuous/)
11. pytest‑homeassistant‑custom‑component – [https://github.com/MatthewFlamm/pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
12. HA conversation batching API – [https://developers.home-assistant.io/docs/intent\_conversation\_api/](https://developers.home-assistant.io/docs/intent_conversation_api/)
13. Assist pipeline WebSocket `listen_for_response` flag – *new*
14. `conversation_id` reuse example – *multi‑turn reference*
15. `vol.In` docs – [https://github.com/alecthomas/voluptuous/blob/master/voluptuous/validators.py](https://github.com/alecthomas/voluptuous/blob/master/voluptuous/validators.py)
16. `QueueHandler`/`QueueListener` async logging pattern – *Python logging cookbook*
17. OpenAI function‑calling responses (content =`null` on tool call) – OpenAI forum post

---

\## 11  Multi‑device & Session Continuation (new)

\### 11.1 Session keys
Sessions are keyed by **(conversation\_id, device\_id)** so five voice satellites can host parallel conversations without collision.

\### 11.2 Focus memory
After every successful `control_device`, *AgentCore* stores:

```python
focus = {"targets": [...], "action": "light.turn_on"}
```

On the next user turn a hidden *system* message reminds the LLM of the context, enabling follow‑ups like “a little brighter please.”

\### 11.3 Unified prompt flow
LLM calls `confirm_action` or `ask_user` **with its own `question` string**.
Those wrappers delegate to **`prompt_user`**, which returns `{"speak": …}`.
`plan_execute` detects `speak`, returns early to *conversation.py*; *ConversationEntity* speaks via `assist_pipeline/run` (`listen_for_response: true`) and saves the session.
The next utterance (same `conversation_id`, `device_id`) resumes at the top of the loop with restored messages.

\### 11.4 Concurrency & logging
All state mutations occur inside the HA event loop; non‑blocking file I/O is handled by `utils.logging.QueueListener`.
