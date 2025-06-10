# Migration to Agent-Based Architecture

This document outlines a proposal for migrating the current workflow in the **Special Agent** component to a more agentic design. The goal is to allow the LLM to plan which tools to use rather than following a rigid sequence of steps. All LLM interactions should utilize the OpenAI API with the **o3** model family.

## 1. Current Architecture Overview

The component integrates with Home Assistant and is organized by responsibility:

| File | Purpose |
| --- | --- |
| `agent_logic.py` | Main orchestrator that handles conversation requests, intent classification, and session management |
| `gpt_commands.py` | Wrappers around OpenAI chat completions (intent classification, command generation, weather responses, etc.) |
| `vector_index.py` | Builds and queries the vector database of Home Assistant entities |
| `entity_refinement.py` | Filters and ranks candidate entities from the vector search |
| `data_sources.py` | Provides device state retrieval and command execution helpers |
| `spotify_integration.py` | Handles Spotify authentication and search queries |
| `weather.py` | Retrieves local and online weather data |
| `command_history.py` | Logs executed commands and responses |

`agent_logic.py` currently calls many of these modules in a fixed order. As functionality grows, this script becomes harder to maintain.

## 2. Proposed Agent Architecture

### 2.1 Agent Core

Introduce a new module `agent_core.py` which defines an `Agent` class responsible for:

1. Maintaining conversation state (pending confirmations, prior tool outputs).
2. Exposing a **tool registry** so the LLM knows which actions are available.
3. Delegating tool execution to existing modules.

```python
# agent_core.py (skeleton)
from typing import Any, Dict, Callable
from . import gpt_commands

class Tool:
    def __init__(self, name: str, description: str, func: Callable[[Dict[str, Any]], Any]):
        self.name = name
        self.description = description
        self.func = func

class Agent:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.tools = {}

    def register_tool(self, tool: Tool):
        self.tools[tool.name] = tool

    def plan(self, user_text: str) -> str:
        """Ask the LLM which tool(s) to run and with what arguments."""
        tool_schema = [f"{t.name}: {t.description}" for t in self.tools.values()]
        prompt = (
            "You are an assistant that decides which tool to call.\n" +
            "Available tools:\n" + "\n".join(tool_schema) +
            "\nBased on the user request, return a JSON plan."
        )
        return gpt_commands.run_openai(prompt, user_text, model="o3")

    def execute_plan(self, plan: dict) -> Any:
        # Iterate through tool calls in the plan and run them
        ...
```

### 2.2 Tools

Each capability is implemented as a small function or class. Existing modules already provide much of the logic:

- **Device Control** – wraps `execute_ha_command` from `data_sources.py`.
- **Vector Search** – wrapper around `load_vector_index` and `query_vector_index` in `vector_index.py`.
- **Spotify Search** – uses `spotify_integration.get_spotify_access_token` and `search_spotify`.
- **Weather Lookup** – calls `weather.fetch_weather_data` and `gpt_commands.generate_weather_response`.
- **Confirmation Prompt** – uses `gpt_commands.generate_user_friendly_confirmation`.

A tool definition might look like:

```python
# Inside agent_core.py
from .data_sources import execute_ha_command

def tool_control_device(params):
    """Execute a Home Assistant service call."""
    return execute_ha_command(params)

agent.register_tool(Tool(
    name="control_device",
    description="Execute a Home Assistant command.\nExpected params: service and data",
    func=tool_control_device
))
```

### 2.3 Conversation Flow

1. `conversation.py` receives the user utterance.
2. It calls `process_conversation_input` in `agent_logic.py`.
3. `agent_logic.py` delegates to an instance of `Agent` from `agent_core.py`:

```python
# agent_logic.py (simplified excerpt)
from .agent_core import Agent

agent = Agent(api_key=openai_api_key)


def process_conversation_input(user_text, device_id, hass):
    if pending_session := pending_dict.get(device_id):
        return agent.handle_confirmation(user_text, pending_session)

    plan_json = agent.plan(user_text)
    plan = json.loads(plan_json)
    result = agent.execute_plan(plan)
    return result, True
```

4. The LLM chooses tools based on the user request, possibly running them iteratively. For example, if a device search returns no results, the agent can issue another search with refined terms.
5. Once a command list is generated, the agent asks for user confirmation using the confirmation tool.
6. Upon confirmation, the execution tool calls Home Assistant services.

## 3. OpenAI Integration

All LLM calls are performed via the OpenAI API using the **o3** model family. Existing helper functions in `gpt_commands.py` can be reused but should default to `model="o3-mini"` or similar. Example utility:

```python
# gpt_commands.py
from openai import OpenAI

CLIENT = OpenAI()

def run_openai(system_prompt: str, user_prompt: str, model: str = "o3-mini") -> str:
    completion = CLIENT.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_prompt},
                 {"role": "user", "content": user_prompt}],
    )
    return completion.choices[0].message.content.strip()
```

## 4. Migration Steps

1. **Create `agent_core.py`** with the `Agent` class and basic tool registry.
2. **Wrap existing functionality** as tools as outlined above.
3. **Refactor `agent_logic.py`** so that it primarily loads configuration, maintains the pending session dictionary, and delegates planning/execution to the `Agent`.
4. **Update tests** to cover the new agent flow. Mocks can simulate tool outputs.
5. Gradually expand the set of tools – e.g., calendar lookups, scene activation – without bloating `agent_logic.py`.

## 5. Benefits

- **Extensibility**: Adding a new skill only requires registering a new tool.
- **Maintainability**: The orchestrator becomes smaller and easier to reason about.
- **User Experience**: The agent can ask clarifying questions when uncertain and retry tool calls with adjusted queries.

---
This plan keeps the existing modules largely intact while enabling a more autonomous agent that reasons with OpenAI's o3 model and chooses from a set of well-defined tools.
