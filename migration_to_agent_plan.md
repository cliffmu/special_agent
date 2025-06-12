# Special Agent — Agent‑Based Migration Plan (v4.0, **self‑contained**)

This document is the **single source of truth** for rebuilding the Special Agent
Home Assistant custom component from a brittle, workflow‑driven prototype into a
modular, tool‑empowered **LLM agent** that supports:

* **Dynamic scene generation**  
  – e.g. “_living‑room cozy_” → dim accent lights to personal 7 %, start fireplace,
  play a chill playlist.
* **Whole‑home commands**  
  – e.g. “_good night_” → turn off *all* lights, TVs, set thermostats to eco.
* **Personal preference learning**  
  – remembers brightness, playlist, and device selections per room & user.
* **Scalable entity retrieval** for > 4 k Home Assistant entities without
  drowning the LLM in tokens.
* **Cost‑aware model routing** between OpenAI **o3‑mini** and the new
  **o3‑pro** Responses API.

It merges every detail from earlier v3.x drafts, so nothing else is required.

---

## 0  High‑level goals & use‑cases

| Goal | Typical voice request | Expected behaviour |
|------|----------------------|--------------------|
| **G‑1** Dynamic scenes | “Make the living‑room cozy.” | Lights ≤ 10 %, fireplace on, ambient music. |
| **G‑2** Global scenes | “Good night.” | All lights + AV off, thermostats eco. |
| **G‑3** Preferences | “Set cozy brightness to 8 %.” | Persists; later “cozy” uses 8 %. |
| **G‑4** Info queries | “What’s tomorrow’s weather?” | Spoken forecast using HA sensors + API. |
| **G‑5** One‑shot controls | “Turn on the kitchen light.” | Confirmation → HA service call. |

---

## 1  Repository layout (HACS‑compatible)

ROOT/
│
├── hacs.json # content_in_root = true
├── manifest.json
├── README.md # brief install + link to this plan
├── init.py # HA entry point (registers service & conversation)
├── conversation.py # ConversationAgent subclass
│
├── agent_core.py # Tool registry, plan(), execute_plan()
│
├── tool_specs/ # 1 file = 1 ToolSpec
│ ├── control_device.py
│ ├── search_devices.py
│ ├── generate_scene.py
│ ├── preference_manager.py
│ ├── get_weather.py
│ ├── search_spotify.py
│ ├── confirm_action.py
│ └── ask_user.py
│
├── utils/
│ ├── openai_client.py # o3‑mini vs o3‑pro wrapper
│ ├── vector_index.py # per‑area FAISS/LanceDB indexes
│ ├── data_sources.py # HA helpers
│ ├── logging.py
│ └── constants.py
│
├── tests/ # pytest + pytest‑homeassistant‑custom‑component
│ ├── test_preferences.py
│ ├── test_scene.py
│ └── ...
│
├── REFERENCE/ # frozen legacy workflow code (read‑only)
└── migration_to_agent_plan.md # this file

yaml
Copy

*`hacs.json` sets `"content_in_root": true`, so HACS treats `ROOT/` as the
component package—no extra `custom_components/` folder needed.*

---

## 2  Coding & naming conventions

| Item | Convention | Example |
|------|------------|---------|
| Modules | `snake_case.py` | `search_devices.py` |
| Tool files | `tool_<verb>.py` recommended but not required | `tool_control.py` |
| Classes | `PascalCase` | `Agent`, `ToolSpec` |
| Voluptuous schemas | inside each ToolSpec.parameters | see `preference_manager.py` |
| Tests | `tests/test_<module>.py` | `test_scene.py` |

---

## 3  Architecture summary

### 3.1 Agent & tool schema

```python
@dataclass
class ToolSpec:
    name: str                  # snake_case
    description: str           # one‑liner
    parameters: vol.Schema     # validated dict
    returns: str | None        # human description
    func: Callable[..., Awaitable]
Agent.plan() renders a system prompt that lists tool_schema then asks
OpenAI (JSON‑mode for mini, Responses for pro) to output:

json
Copy
{
  "steps": [
    {"tool": "search_devices", "params": {"query": "kitchen lights"}, "save_as": "devices"},
    {"tool": "confirm_action", "params": {"action": "turn on", "targets": "$devices"}},
    {"tool": "control_device", "params": {"service": "light.turn_on", "data": {"entity_id": "$devices"}}}
  ]
}
Agent.execute_plan() iterates steps, handles confirmation / clarification
pauses, feeds each result back to the LLM (ReAct) until the plan yields
tool == "respond" or a guard‑rail abort.

3.2 Cost‑aware model routing
python
Copy
def choose_model(user_text, first_tool):
    if len(user_text) > 120 or first_tool == "generate_scene":
        return "o3-pro-latest"   # Responses API
    return "o3-mini"
utils/openai_client.py hides the difference between Chat and Responses APIs.

3.3 Entity retrieval strategy
Build‑time filtering
Keep only domains light, switch, media_player, climate, cover.
RegEx skip patterns: *_led, *.bass_*, *.treble_*, *.color_*.

Per‑area sub‑indexes
vector_index/<area_id>/ → FAISS index of ≤ 200 entities each.

Hierarchical search
For global requests iterate areas; for room‑specific pass area_id.

4  Tool inventory (initial + extended)
Tool	Main duties
search_devices	cosine search of (sub)index, returns list of entity_ids
control_device	wraps hass.services.async_call
generate_scene	build multi‑service scene or call scene.create
preference_manager	get/set per‑user defaults JSON
get_weather	sensor + API, formats speech
search_spotify	fetch best URI
confirm_action	yes/no confirmation
ask_user	open‑ended clarification
(future) calendar, energy, reminders…	drop‑in ToolSpec

5  Prompt essentials (system)
arduino
Copy
You are Special Agent, a smart‑home AI.
TOOLS:
{{tool_schema}}
RULES:
- If request implies "cozy", "movie", "good night", call generate_scene.
- Use preference_manager.get for defaults, fall back to sensible presets.
- For state changes ALWAYS insert confirm_action before control_device.
- Ignore entities with IDs ending "_led" or containing ".bass_", ".treble_".
- Output ONLY valid JSON per schema; no extra text.
6  Implementation roadmap & smoke‑tests
Phase	Deliverable	Coding tasks	Smoke‑test
0 Repo bootstrap	Structure & REFERENCE	move legacy, scaffold folders	hacs.reload loads
1 Agent skeleton	agent_core.py, no tools	“Hi” → “can’t help yet”	
2 Control MVP	tools: search_devices, control_device, confirm_action; wire conversation	“Turn on kitchen light” → confirmation + action	
3 Info tools	get_weather, search_spotify	“Weather?” spoken forecast	
3b Preference mgr	preference_manager + UI helper	“Set cozy brightness 8 %” persists	
4 Clarification loop	ask_user + handler	“Turn on lights” → asks room	
4b Scene tool	generate_scene	“Living‑room cozy” dims to pref brightness	
4c Global scene	hierarchical area loop	“Good night” shuts house down	
5 ReAct loop	feed tool outputs back to LLM	Complex multi‑step request succeeds	
5b o3‑pro router	wrapper & heuristic	Long prompt logs o3-pro	
5c Sub‑index	build per‑area indexes	query latency < 150 ms	
6 Safety & multi‑device	3‑iteration cap, session key `mac	conv_id`	two speakers run parallel confirmations
7 Test harness	pytest + fixtures, CI	pytest -q passes	
7b Extra tests	preference recall, LED exclusion, routing	automated	
8 Docs & guide	/docs/adding_tool.md, README update	docs render	
9 Advanced tools	calendar, scenes, energy stats	auto‑discovered by agent	

7  Expanded test matrix
ID	Scenario	Expected
T‑1	Cozy preference recall	Stores & uses 8 % brightness
T‑2	LED exclusion	“lights off” leaves speaker_led untouched
T‑3	Whole‑home off	Good‑night plan ≤ 2 k tokens, executes all
T‑4	Router	short vs long prompt model selection
T‑5	Multi‑device	Device‑A confirmation doesn’t block Device‑B
T‑6	ReAct loop depth	caps at 3 iterations then aborts politely

8  Persisted data formats
jsonc
Copy
// config/.special_agent_prefs.json
{
  "user_id_abc": {
    "living_room": {
      "cozy_brightness": 8,
      "cozy_playlist_uri": "spotify:playlist:123"
    }
  }
}
jsonc
Copy
// vector_index/living_room/index_meta.json
{
  "area_id": "living_room",
  "created": "2025‑06‑12T14:00Z",
  "entity_count": 185
}
9  OpenAI usage & cost estimate
Model	Typical prompt	Token avg	Cost per req
o3‑mini	one‑shot control	850	$0.001
o3‑pro	scene / global	4 k	$0.015

Router keeps monthly cost ≈ $4 for 500 control + 100 scene requests.

10  References & inspirations
HACS “content_in_root” spec (hacs.xyz)

Home Assistant Area registry & Scenes docs

OpenAI o3‑pro Responses API docs (2025‑06)

ReAct: Yao et al., ICLR 2023

HA community posts on sub‑area light control & LED exclusion

Saver custom component (example JSON state)

pytest‑homeassistant‑custom‑component

---

## References
- Anthropic – Building effective agents - https://www.anthropic.com/engineering/building-effective-agents
- ReAct pattern paper (Yao et al. 2022) - https://arxiv.org/abs/2210.03629
- OpenAI JSON mode / Structured Outputs examples - https://platform.openai.com/docs/guides/structured-outputs/examples
- Home Assistant ConversationEntity dev docs - https://developers.home-assistant.io/docs/core/entity/conversation/
- Home Assistant LLM API tools docs - https://developers.home-assistant.io/docs/core/llm/
- LangChain tool‑calling guidelines - https://python.langchain.com/docs/concepts/tool_calling/
- Spotify Web API search reference - https://developer.spotify.com/documentation/web-api/reference/search
- Home Assistant service action example - https://developers.home-assistant.io/docs/dev_101_services/
- ArXiv ReAct abstract (iteration benefits) - https://arxiv.org/abs/2210.03629
