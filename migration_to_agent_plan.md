# Migration to Agent-Based Architecture – **Special Agent** (v2)

> **TL;DR**
> Replace the rigid intent-classifier workflow with an LLM-driven *agent* that plans and iteratively executes **tools**.
> Each capability (weather lookup, Spotify search, HA service call, etc.) is exposed as a strongly-typed tool; the LLM chooses and sequences them in JSON.
> Guardrails include mandatory confirmation for state-changing actions, optional clarification questions, loop limits, and exhaustive logging.
> The design stays 100% compatible with Home Assistant’s Conversation / LLM API and is easily extended by **registering new tools**.

---

## 1. Why move from workflow to agent?

| Aspect         | Current workflow                               | Agentic design                                                      |
| -------------- | ---------------------------------------------- | ------------------------------------------------------------------- |
| Logic          | Fixed `if / else` branches in `agent_logic.py` | LLM plans which tools to call (may loop)                            |
| Extensibility  | Each new feature ↔ new branch                  | Add a tool ⇒ done                                                   |
| Maintenance    | Growing, brittle orchestrator                  | Small orchestrator; logic lives in tools & prompt                   |
| Transparency   | Hard to see *why* an action ran                | Plan JSON + logs expose reasoning                                   |
| Cost / latency | Single big LLM call                            | Potentially more calls → mitigated with `o3-mini` for trivial tasks |

Agents are ideal when you **can’t pre-enumerate steps**, but they *must* expose clear tools, keep loops short, and surface plans for inspection.

---

## 2. Core principles & guardrails

1. **Simple, typed tool set** – clear names, descriptions, JSON schemas
2. **Iterative ReAct loop** – *Reason → Act → Observe* until goal met or max N iterations
3. **Mandatory confirmation** before any HA state change
4. **Optional clarification** tool when user request is ambiguous
5. **Safety stops** – break after 3 failed or unanswered iterations
6. **Full logging** (user text, plan, tool outputs, final answer) for audit & debugging
7. **Stay within HA conversation API** so it works with voice & Assist

---

## 3. Unified tool schema

```python
@dataclass
class ToolSpec:
    name: str                     # snake_case
    description: str              # one-line “when / what”
    parameters: vol.Schema        # voluptuous schema for params
    returns: str | None           # human description; agent needn’t parse
    func: Callable[..., Awaitable]# async implementation
```

**Example – Device control**

```python
tool_control_device = ToolSpec(
    name="control_device",
    description="Call a Home Assistant service to change state. Params: {service:str, data:dict}",
    parameters=vol.Schema({
        vol.Required("service"): str,
        vol.Optional("data", default={}): dict,
    }),
    returns='"OK" on success or an error string',
    func=execute_ha_command,
)
```

Each new capability becomes a new `ToolSpec` registered on startup; the agent auto-discovers them.

---

## 4. Agent planning protocol

### 4.1 Prompt skeleton (system)

```
You are Special Agent, a smart-home assistant.
TOOLS:
{{tool_schema}}
RULES:
- If user request *changes* HA state, you MUST insert a confirm_action step before executing control_device.
- If request is ambiguous, insert ask_user step.
- Output ONLY valid JSON matching the schema below.

JSON_SCHEMA = {
  "steps": [
    {
      "tool": "<tool_name>",
      "params": { ... },
      "save_as": "<var>"?
    }
  ]
}
```

### 4.2 Example plan

```json
{
  "steps": [
    {"tool": "search_devices",
     "params": {"query": "kitchen lights"},
     "save_as": "targets"},

    {"tool": "confirm_action",
     "params": {"action": "turn on", "targets": "$targets"}},

    {"tool": "control_device",
     "params": {"service": "light.turn_on",
                "data": {"entity_id": "$targets"}}}
  ]
}
```

The runner replaces "$targets" with the actual list returned by the earlier step. JSON‑mode on the OpenAI API ensures valid output.

---

## 5. Conversation flow in Home Assistant

```
Voice / UI → conversation.py → process_conversation_input()
   ↳ If pending confirmation / clarification:
        → handle_*_response()
   ↳ else:
        → Agent.plan(user_text)        # LLM call
        → json.loads(...)
        → Agent.execute_plan(plan)     # loops & tool calls
             ↳ confirm_action → stores session → ask user → RETURN
             ↳ ask_user       → stores session → ask user → RETURN
             ↳ completed      → returns final speech or “Done.”
```

Agent uses HA’s hass.async_add_executor_job (or native async) so the event loop is never blocked. The design matches the ConversationEntity pattern and can, in future, plug into HA’s official LLM API tool‑passing mechanisms.

---

## 6. Key tools (initial set)

| Tool             | Purpose                            | Depends on                         |
| ---------------- | ---------------------------------- | ---------------------------------- |
| `search_devices` | Vector search ↔ entity IDs         | `vector_index.py`                  |
| `control_device` | `hass.services.async_call` wrapper | `data_sources.py` & HA service API |
| `get_weather`    | Return friendly weather summary    | `weather.py`                       |
| `search_spotify` | Return best Spotify URI for query  | Spotify Web API                    |
| `confirm_action` | Produce Y/N question               | `gpt_commands`                     |
| `ask_user`       | Open-ended clarification           | simple template or LLM             |

---

## 7. Error handling & loop limits

* Any tool exception ⇒ break loop, apologize to user
* Max 3 iterations (ReAct pattern) to avoid runaway planning
* If LLM emits unknown tool, reply “Sorry, I can’t do that yet.”
* All actions logged to `command_history.json` (plan + outputs) for traceability

---

## 8. Implementation roadmap (task list)

| Phase | Milestone                   | Concrete tasks                                                                                                              |
| ----- | --------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **0** | **Scaffolding**             | `agent_core.py` with `ToolSpec`, `Agent.register_tool`, `Agent.plan`, `Agent.execute_plan`                                  |
| **1** | **Minimal tools**           | Implement & register: `search_devices`, `control_device`, `confirm_action`                                                  |
| **2** | **Conversation wiring**     | Refactor `agent_logic.py` to delegate to Agent; add pending‑session dict & handlers                                         |
| **3** | **Info tools**              | Add `get_weather`, `search_spotify`; adjust prompt examples                                                                 |
| **4** | **Clarification loop**      | Implement `ask_user` tool, `handle_clarification_response`; unit tests                                                      |
| **5** | **Iterative ReAct**         | Modify `Agent.execute_plan` to feed each tool result back to LLM when `steps` is empty, enabling true observe‑reason cycles |
| **6** | **Safety & metrics**        | Loop limit, exception wrappers, additional logging; per‑request cost & latency metrics                                      |
| **7** | **Extensibility docs**      | Write `docs/adding_tool.md` with template, param schema, test pattern                                                       |
| **8** | **Advanced tools (future)** | Calendar, scene control, energy stats, etc. just by registering new ToolSpecs                                               |

Each milestone can be given to a coding AI assistant (“Implement milestone 2: …”) for incremental PRs.

---

## 9. Extending the system

* Add a tool: Implement function → define ToolSpec → agent.register_tool → optional example in prompt.
* Tune prompts: Evaluate logs; adjust system prompt examples; keep it concise.
* Swap models: o3-mini for cheap tasks; full o3 for deep reasoning—Agent constructor takes model_name.

---

## 10. Reference links

* Anthropic – Building effective agents - [anthropic.com](https://www.anthropic.com/engineering/building-effective-agents)
* ReAct pattern paper - [arxiv.org](https://arxiv.org/abs/2210.03629)
* OpenAI function-calling docs - [platform.openai.com](https://platform.openai.com/docs/assistants/tools/function-calling?utm_source=chatgpt.com)
* OpenAI JSON mode - [https://platform.openai.com/docs/guides/structured-outputs/examples](https://platform.openai.com/docs/guides/structured-outputs/examples?api-mode=responses)
* Home Assistant ConversationEntity dev docs - https://developers.home-assistant.io/docs/core/entity/conversation/
* Home Assistant LLM API tools docs - https://developers.home-assistant.io/docs/core/llm/
* LangChain tool‑calling guidelines - https://python.langchain.com/docs/concepts/tool_calling/
* Spotify Web API search reference - https://developer.spotify.com/documentation/web-api/reference/search
* Home Assistant service action example - https://developers.home-assistant.io/docs/dev_101_services/
* ArXiv ReAct abstract (iteration benefits) - https://arxiv.org/abs/2210.03629
