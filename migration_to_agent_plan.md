# Migration to Agent‑Based Architecture – **Special Agent v3**

## Quick Overview

1. Archive the existing workflow code under `/REFERENCE/` so the coding‑LLM can still inspect it.
2. Create a clean, nested folder layout (`special_agent/…`) that follows HACS + Python best‑practice structure. ([docs.python-guide.org](https://docs.python-guide.org/writing/structure/))
3. Incrementally rebuild the core as a **tool‑driven agent** using OpenAI JSON‑mode/function‑calling and the ReAct loop. ([community.openai.com](https://community.openai.com))
4. After every milestone, run a manual "voice → LLM → HA → TTS" test to ensure we always have a working foundation.

---

## Repository Restructuring

```
repo-root/
│
├─ REFERENCE/ # frozen snapshot of legacy workflow code
│  └─ (all current files stay here, READ-ONLY)
│
├─ custom_components/
│  └─ special_agent/ # brand-new agentic implementation lives here
│     ├─ __init__.py
│     ├─ agent_core.py
│     ├─ tool_specs/
│     │  ├─ control_device.py
│     │  ├─ search_devices.py
│     │  ├─ get_weather.py
│     │  └─ ...
│     ├─ utils/
│     │  ├─ vector_index.py
│     │  ├─ data_sources.py
│     │  └─ logging.py
│     ├─ conversation.py
│     └─ tests/
│        └─ (pytest files)
│
└─ migration_to_agent_plan.md # this plan
```

**Why?** HACS integrations must live under `custom_components/<domain>` and contain **all runtime code** ([hacs.xyz](https://hacs.xyz/docs/publish/integration)), while archiving the old workflow in `/REFERENCE/` lets the coding assistant compare implementations.

---

## Unified Naming & Coding Conventions

| Element            | Convention                                        | Rationale                                                                                        |
| ------------------ | ------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Python modules     | `snake_case.py`                                   | PEP‑8                                                                                            |
| Tool files         | `tool_<verb>.py` (e.g., `tool_control_device.py`) | Self‑documenting                                                                                 |
| Classes            | `PascalCase`                                      | PEP‑8                                                                                            |
| Constants          | `SCREAMING_SNAKE`                                 | PEP‑8                                                                                            |
| Tests              | `test_<module>.py` inside `tests/`                | pytest autodiscovery ([pytest.org](https://docs.pytest.org))                                     |
| Voluptuous schemas | Defined in each `ToolSpec.parameters`             | Matches HA config style ([home-assistant.io](https://www.home-assistant.io/docs/configuration/)) |

---

## Core Agent & Tool Schema

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: vol.Schema
    returns: str | None
    func: Callable[..., Awaitable]
```

Each new capability = one `ToolSpec` in `tool_specs/` → `Agent.register_tool()` at load time.

---

## Revised Phased Roadmap with Manual Test Points

| Phase | Deliverable            | Coding Tasks                                                                                                                                      | Manual Test (voice → action)                                                                    |
| ----- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| 0     | Repo bootstrap         | Move legacy code to `/REFERENCE/`<br>Scaffold new folder tree                                                                                     | Verify HACS still detects integration (legacy).                                                 |
| 1     | Agent skeleton         | `agent_core.py` with Agent, ToolSpec, `plan()` (OpenAI JSON-mode) and `execute_plan()` (no loops yet).                                            | Speak: “Hello” → expect polite error message.                                                   |
| 2     | Device-control MVP     | Implement tools: `search_devices`, `control_device`, `confirm_action`.<br>Wire `conversation.py` to new Agent.                                    | Speak: “Turn on kitchen light” → confirm & toggle HA light.                                     |
| 3     | Information tools      | Add `get_weather`, `search_spotify`. Expand prompt examples.                                                                                      | Speak: “What’s the weather?” → verbal forecast.<br>“Play jazz in living room” → Spotify via HA. |
| 4     | Clarification loop     | Implement `ask_user` tool.<br>Store `original_request` and re-plan after answer.                                                                  | Speak: “Turn on lights” → agent clarifies room, then executes.                                  |
| 5     | Iterative ReAct loop   | Modify `execute_plan` for iterative reasoning (ReAct pattern). ([arxiv.org](https://arxiv.org/abs/2210.03629))                                    | Complex conditional voice command execution.                                                    |
| 6     | Safety & multi-device  | 3-iteration limit, unknown tool handling.<br>Multi-device session isolation. ([community.home-assistant.io](https://community.home-assistant.io)) | Verify session isolation per MAC and `conversation_id`.                                         |
| 7     | Test harness           | pytest setup with custom fixtures ([pytest-homeassistant](https://github.com/home-assistant/pytest-homeassistant-custom-component)).              | `pytest -q` locally & CI passing.                                                               |
| 8     | Docs & extension guide | Add clear docs (`docs/adding_tool.md`). Update `README`.                                                                                          | Ensure copy-paste readiness.                                                                    |
| 9     | Advanced tools         | Calendar, scenes, energy as standalone `ToolSpecs`.                                                                                               | Confirm tools auto-appear and function.                                                         |

---

## Multi-device Voice Sessions

* Device-ID logic expanded: `f"{source_device_mac}|{conversation_id}"` ensures isolation per device.
* Pending sessions stored: `hass.data[DOMAIN]["pending"][session_id]`.

---

## Confirmation & Clarification UX

* `confirm_action` always asks yes/no before actions ([community.home-assistant.io](https://community.home-assistant.io)).
* `ask_user` poses open questions; abort after 15 s timeout.
* Future: actionable notifications, Assist pre-prompt.

---

## Testing Checklist per Milestone

* Voice path: wake-word → STT → HA Assist → Agent → TTS.
* Logs: `command_history.json` tracks interactions.
* Unit tests (`pytest -q`): pass.
* Service calls: verified in HA Developer Tools → Events.
* Recovery: agent timeout upon mic disconnect.

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
