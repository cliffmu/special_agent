# Special Agent — Agent‑Based Migration Plan (v5.2, **self‑contained**)

*This file supersedes every earlier draft (v4.0 and v5.1).  
It preserves all content present in v4.0, retains the deeper tool specs from v5.0/5.1, **adds the new `learn_preferences` tool**, and relegates heavier ML features to a far‑future roadmap phase.*

---

## 0  Project goals & use‑cases (unchanged)

| ID | Goal | Typical voice request | Outcome |
|----|------|----------------------|---------|
| G‑1 | Dynamic room scenes | “Make the living‑room cozy.” | Dim lights to personal %; fireplace + playlist |
| G‑2 | Whole‑home scenes | “Good night.” | House shut‑down across all areas |
| G‑3 | Preference learning | “Set cozy brightness to 8 %.” or implicit fine‑tune | Stored; reused next time |
| G‑4 | Info queries | “Weather tomorrow?” | Spoken forecast |
| G‑5 | One‑shot controls | “Turn on the kitchen light.” | Confirmation → action |

---

## 1  Repository layout (HACS‑compatible)

```
ROOT/
├─ hacs.json          # "content_in_root": true :contentReference[oaicite:0]{index=0}
├─ manifest.json
├─ README.md
├─ __init__.py
├─ conversation.py    # ConversationEntity subclass :contentReference[oaicite:1]{index=1}
│
├─ agent_core.py
│
├─ tool_specs/
│   ├─ control_device.py
│   ├─ search_devices.py
│   ├─ generate_scene.py
│   ├─ preference_manager.py
│   ├─ confirm_action.py
│   ├─ ask_user.py
│   ├─ get_weather.py
│   ├─ search_spotify.py
│   ├─ learn_preferences.py     # ★ NEW
│   ├─ area_iterator.py
│   └─ build_vector_index.py
│
├─ utils/
│   ├─ openai_client.py
│   ├─ vector_index.py
│   ├─ data_sources.py
│   ├─ logging.py
│   └── constants.py
│
├─ tests/                       # pytest‑homeassistant‑custom‑component :contentReference[oaicite:2]{index=2}
│   ├─ test_preferences.py
│   ├─ test_scene.py
│   └─ ...
│
├─ REFERENCE/
└─ migration_to_agent_plan.md   # this file
```

---

## 2  Coding & naming conventions

| Artifact | Convention | Example |
|----------|------------|---------|
| Modules  | `snake_case.py` | `search_devices.py` |
| Classes  | `PascalCase` | `PreferenceManager` |
| Tool files | optional `tool_<verb>.py` | `tool_control_device.py` |
| Voluptuous schemas | inside each `ToolSpec.parameters` :contentReference[oaicite:3]{index=3} | `brightness_pct: vol.All(vol.Coerce(int), vol.Range(0,100))` |
| Tests    | `tests/test_<module>.py` | `test_scene.py` |

---

## 3  Architecture summary

### 3.1 Agent & tool schema
```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: vol.Schema
    returns: str | None
    func: Callable[..., Awaitable]
```
*Plan* vs *Execute* (ReAct) loop retained :contentReference[oaicite:4]{index=4}.

### 3.2 Cost‑aware model routing  
Heuristic unchanged; o3‑pro pricing: $20 /M in, $80 /M out vs $2 / $8 for o3‑mini :contentReference[oaicite:5]{index=5}.

### 3.3 Entity retrieval & vector indexes  
Global + per‑area NumPy sub‑indexes; auto‑refresh via `build_vector_index` :contentReference[oaicite:6]{index=6}.

---

## 4  Tool catalogue (ranked)

| Rank | Tool | Inputs | Internal operations | LLM calls | Returns |
|------|------|--------|---------------------|-----------|---------|
| 1 | `control_device` | `service, data` | `hass.services.async_call`  | none | `"OK"` / error |
| 2 | `confirm_action` | `action, targets` | Formats question; tiny LLM for wording | **mini** (< 50 tok) | question |
| 3 | `search_devices` | `query, area?, k` | FAISS cosine search on **area sub‑index**  | none | `[entity_id]` |
| 4 | **`generate_scene`** | `intent, area` | (a) `preference_manager.get`; (b) 3×`search_devices` (lights, media_player, switches); (c) compose command list; (d) optional `scene.create`  | none | `commands_list` |
| 5 | **`area_iterator`** | `intent` | Loops HA area registry , calls `generate_scene`; dedup; merge | none | big `commands_list` |
| 6 | `preference_manager` | `user, area, key, mode` | Read/modify JSON store  | none | value |
| 7 | `build_vector_index` | `{force?:bool}` | Loads HA states; filters domains; builds / updates FAISS index on disk; stores timestamp | none | `"rebuilt"` |
| 8 | `ask_user` | `question` | Stores pending session | none | question |
| 9 | `get_weather` | `location?` | Sensor + external API; template answer  | **mini** (< 100 tok) | forecast |
|10 | `search_spotify` | `query, type` | HTTP to Spotify `/search`  | none | URI |
|11 | *(future)* `calendar_lookup` | `date` | Google / HA calendar API | mini | events |
|12 | *(future)* `energy_report` | `span` | HA statistics API | none | stats |
|13 | *(future)* `diagnostic_tool` | `entity_id` | last‑updated, availability | none | health JSON |


### 4.1 `learn_preferences` design

*Use‑case A – session auto‑save*  
When several brightness / colour tweaks hit the same light within 5 min, the final state is saved by calling:  
```json
{ "tool": "learn_preferences",
  "params": {
    "area": "living_room",
    "entity_id": "light.lamp",
    "prefs": { "brightness_pct": 12 },
    "mode": "merge"
}}
```

*Use‑case B – explicit user command*  
“Remember current settings for the office as ‘focus’ scene.”

Agent flow: ask clarification → map to scene intent → call `learn_preferences` with `mode:"replace"`.

---

## 5  Prompt essentials (system)

```
You are Special Agent, a smart‑home AI.
TOOLS:
{{tool_schema}}
RULES:
- If request implies "cozy", "movie", "good night", call generate_scene.
- After a fine‑tune session OR explicit request, call learn_preferences.
- For state changes ALWAYS confirm_action before control_device.
- Exclude *_led, *.bass_*, *.treble_* entities.
- Output ONLY valid JSON.
```

---

## 6  Implementation roadmap & smoke‑tests

| Phase | Deliverable | Status / key tests |
| ----- | ----------- | ------------------ |
| 0 | **Repo bootstrap** | ✅ HACS loads component :contentReference[oaicite:2]{index=2} |
| 1 | **Agent skeleton** (no tools) | ✅ “Hi” → “can’t help yet” |
| 1b | **`build_vector_index` (foundational)** – pull HA states, chunk, embed, write global NumPy index | ✅ index file exists; `search_devices` returns results |
| 2 | **Control MVP** (`search_devices`, `control_device`, `confirm_action`) | → test *kitchen light* |
| 2b | **Nightly index refresh** (CLI cron calling `build_vector_index --force`) | → “rebuild database” |
| 3 | **Info tools** (`get_weather`, `search_spotify`) | → spoken weather query |
| 3b | **`preference_manager`** | → set + recall cozy brightness |
| 4 | **Clarification loop** (`ask_user`) | → ambiguous “turn on lights” |
| 4b | **`generate_scene`** (LLM‑orchestrated, thin wrapper) | → living‑room cozy |
| 4c | **`area_iterator`** | → whole‑home “good night” |
| 5 | **ReAct loop** – stream tool outputs back to LLM | → complex multi‑step |
| 5b | **o3‑pro router** – cost‑aware model switch | → long prompt selects pro |
| 5c | **Per‑area sub‑index build** (`vector_index/<area>/`) | → per‑area latency < 150 ms |
| 5d | **`learn_preferences`** + session detector | → last tweak persisted |
| 6 | **Safety & multi‑device dedupe** (3‑iteration cap) | → two speakers parallel |
| 7 | **Test harness** (`pytest‑homeassistant`) | → all unit tests pass :contentReference[oaicite:3]{index=3} |
| 8 | **Docs & contributor guide** | → README / /docs updated |
| 9 | **Advanced tools** (calendar, energy, diagnostics) | → drop‑in `ToolSpec` |
| 10 | **Future R&D** – opt‑in ML‑based proactive automation | shadow‑mode predictions |


---

## 7  Expanded test matrix

| ID | Scenario | Expected |
|----|----------|----------|
| T‑1 | Cozy preference recall | uses stored 8 % |
| T‑2 | LED exclusion | leaves `_led` entities |
| T‑3 | Whole‑home off | plan ≤ 2 k tokens |
| T‑4 | Router heuristic | long prompt → o3‑pro |
| T‑5 | Fine‑tune session auto‑save | last tweak persisted |
| T‑6 | Explicit remember command | prefs saved in “replace” mode |
| T‑7 | ReAct depth limit | aborts after 3 rounds |

---

## 8  Persisted data formats

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

---

## 9  OpenAI usage & cost estimate

| Model | Typical prompt | Avg tokens | Cost / req |
|-------|----------------|-----------|------------|
| **o3‑mini** | one‑shot control | ~850 | $0.001 |
| **o3‑pro** | large scene | ~4 k | $0.015 |

Routing keeps monthly cost ≈ $4 for 500 control + 100 scene requests. :contentReference[oaicite:9]{index=9}

---

## 10  References

1. HACS “content_in_root” flag – https://www.hacs.xyz/docs/publish/integration/ :contentReference[oaicite:10]{index=10}  
2. Home Assistant Area registry docs – https://developers.home-assistant.io/docs/area_registry_index/ :contentReference[oaicite:11]{index=11}  
3. ConversationEntity dev guide – https://developers.home-assistant.io/docs/core/entity/conversation/ :contentReference[oaicite:12]{index=12}  
4. Recorder integration (history API) – https://www.home-assistant.io/integrations/recorder/ :contentReference[oaicite:13]{index=13}  
5. OpenAI structured outputs – https://platform.openai.com/docs/api-reference/responses/create :contentReference[oaicite:14]{index=14}  
6. OpenAI o3‑pro pricing – https://community.openai.com/t/o3-is-80-cheaper-and-introducing-o3-pro/1284925 :contentReference[oaicite:15]{index=15}  
7. Medium guide on metadata filtering – https://medium.com/@dmitri.mahayana/ultimate-semantics-search-part-2-metadata-filtering-05cad97bc5da :contentReference[oaicite:16]{index=16}
8. Spotify Web API search – https://developer.spotify.com/documentation/web-api/reference/search :contentReference[oaicite:17]{index=17}  
9. Home Assistant scenes docs – https://www.home-assistant.io/docs/scene/ :contentReference[oaicite:18]{index=18}  
10. Voluptuous validation library – https://pypi.org/project/voluptuous/ :contentReference[oaicite:19]{index=19}  
11. pytest‑homeassistant‑custom‑component – https://github.com/MatthewFlamm/pytest-homeassistant-custom-component :contentReference[oaicite:20]{index=20}  
12. Home Assistant conversation batching API – https://developers.home-assistant.io/docs/intent_conversation_api/ :contentReference[oaicite:21]{index=21}  
