Details for the migration from prototype to new AI agentic version are in docs/migration_to_agent_plan.md. Look at that file first as it details the project, features and development roadmap.

I have a prototype version of the project saved in REFERENCE folder. This folder is just for reference and should not be edited. Look at the REFERENCE folder for examples of how certain code worked but I'm working on migrating this project to an AI agent based tool. Do not use code in the REFERENCE folder to replicate it unless its needed, im trying to improve off the prototype so I dont want to carry issues over to new version. 

### Plex setup flexibility

- **Context**: Plex environments vary widely (server names, library structures, entity domains). Avoid baking in assumptions that break across installations.
- **Entity handling**: Plex controls may surface as different Home Assistant domains (e.g., `media_player`, `button`). We added an entity exception to allow Plex buttons when appropriate. Prefer capability checks over strict domain checks where possible.
- **Configuration**: Make Plex-related entities and identifiers configurable. Support multiple Plex servers and heterogeneous device mappings.
- **Action**: When adding new Plex tools or logic, ensure they work with both players and button-style entities, and document any required configuration.

---

## Code Organization Principles

### Architecture Philosophy: Readability Through Separation

**Core Principle**: Code should read like a story, not a technical manual.

**`agent_core.py` Purpose**:
- **Orchestrator, not implementer** - coordinates components, doesn't contain implementation details
- **~150-200 lines max** - if longer, extract logic to utils
- **Reads like pseudocode** - function calls with descriptive names that explain what's happening
- **Example of good orchestration**:
  ```python
  client = await get_openai_client(hass)
  system_prompt = await build_system_prompt(tools, hass, session_key, ...)
  messages, session_state = load_session(...)
  
  while not done:
      response = await call_llm(client, messages, tools, ...)
      if response.has_final_answer:
          return response.final_text
      if response.has_tool_calls:
          results = await validate_and_execute_tools(...)
  ```

**Utils Philosophy - Extend, Don't Proliferate**:
- **Before creating a new util, check if existing utils can be extended**
- **Cohesive grouping**: Related functions stay together
  - `llm_client.py`: All LLM interaction (calling, errors, metrics)
  - `response_utils.py`: All response handling (parsing, tool execution, results)
  - `tool_registry.py`: All tool management (specs, loading, conversion)
  - `prompt_builder.py`: All prompt construction (context, formatting, composition)
- **Clear boundaries**: Each util has ONE clear responsibility
- **Descriptive names**: File name tells you exactly what's inside
- **Target size**: 150-400 lines per util (if larger, consider splitting by sub-responsibility)

**When to Create a New Util**:
✅ **Create** if:
  - No existing util covers this responsibility
  - The logic is >100 lines and self-contained
  - It would improve readability of agent_core or tools

❌ **Don't create** if:
  - An existing util could be extended logically
  - It's <50 lines (consider adding to existing util)
  - It would fragment related functionality

**Refactoring Process**:
1. Identify complex logic in agent_core (>50 lines doing one thing)
2. Check if existing utils can absorb it
3. If yes: extend existing util with clear function name
4. If no: create new util with focused responsibility
5. Update agent_core to call the new function

### Tools Should Be Simple & Task-Specific
- **Tools** (`tool_specs/*.py`) should be thin wrappers (~50-150 lines)
- **One tool = one specific task** - don't embed orchestration logic
- **Let agent decide the sequence** - tools are building blocks, not workflows
- **Move complex logic to `utils/`** - business logic, API calls, data processing
- **Reuse existing utils** before creating new ones
- **Example:** `play_plex_media` pushes content to client (simple). Agent handles setup separately (power on, open app, scan) using `control_device` and other tools. This allows agent to discover, adapt, and learn what sequence works.
- **Anti-pattern:** Tools that "auto-setup" or orchestrate multiple steps internally - this removes agent's ability to learn and adapt

### Agent Core Should Be Readable
- **`agent_core.py`** should contain high-level orchestration only
- **Target: ~150 lines** for main loop + setup
- Human-readable flow: setup → loop (call LLM → execute tools) → return
- **Every function call should be self-documenting** - reader knows what it does from name alone
- **Move complex logic to `utils/`** - session management, LLM calling, tool execution, prompt building
- **Anti-pattern**: Multi-page functions, inline implementations, complex conditionals

### Tool-Specific Prompts Stay in Tools
- **Tool descriptions** should contain tool-specific guidance and workflows
- **System prompt** (`agent_core.py`) should be general agent behavior only
- **Why:** When tools are disabled, their guidance disappears automatically
- **Example:** Scene memory flow guidance is in `get_scene` and `set_scene` descriptions, not system prompt

### Scene Memory Integration
- **Scene memory learns from agent's discoveries** - agent composes sequences, tests them, saves what works
- **Tools stay dumb** - they execute single tasks without embedded logic
- **Agent stays smart** - discovers setup sequences, adapts to different devices, saves successful patterns
- **Why:** Allows agent to handle varied setups (Apple TV vs Roku vs complex AV) without hardcoding device-specific logic in tools
- **Example:** Plex playback varies by device (some need scan button, some don't; different delay timings). Agent discovers and saves working pattern per room.

### Documentation
- **DO NOT create new `.md` files** unless explicitly requested by user
- Update existing docs: `README.md`, `AGENTS.md`, `docs/scene_plan_latest.md`
- Keep docs synchronized with implementation changes

### Manual test checklist for every development change

- Maintain `docs/manual_test_checklist.csv` whenever development changes user-visible behavior. Add practical checks for new features and regressions alongside the implementation, before committing or installing it.
- Each row needs a stable test ID, feature/version, scenario, exact question or voice command to ask, setup/additional actions, expected device result, expected Live/agent response, failure signs, and editable status, actual-result/notes, and test-date fields.
- Include normal success and relevant failure cases. For device commands, check both the physical/HA result and the spoken confirmation; flag false "device status unavailable" reports and unsupported success claims.
- Use friendly names or clearly marked placeholders such as `[room]`; do not assume a particular Home Assistant entity, device mapping, or Plex setup.
- Start new rows at `Not run`. The user can set `Pass`, `Fail`, `Blocked`, or `Not applicable`. Automated tests do not count as the user's manual test results.
- Preserve existing test IDs and the user's status, notes, and dates. If behavior changes enough to require a retest, add a new versioned row rather than clearing completed results.
- Keep the CSV valid UTF-8 with one header row and consistently quoted fields. Link to the checklist in the completion message and explain which new checks are relevant. Do not create a new checklist file for each task.

---

## Prompt & Tool Description Optimization

### Philosophy: Speed & Clarity Over Tokens

**Priority Order:**
1. **Reduce hallucinations** - clear, non-conflicting instructions
2. **Improve speed** - smaller context = faster processing
3. **Reduce cost** - secondary benefit of smaller context

### Tool Description Structure

**Tool descriptions are sent to LLM in this format:**
```json
{
  "type": "function",
  "function": {
    "name": "tool_name",
    "description": "HIGH-LEVEL GUIDANCE",
    "parameters": {
      "properties": {
        "param": {"description": "PARAM DETAILS"}
      }
    }
  }
}
```

**Description Should Answer:**
- ❓ **WHEN** to use this tool (conditions, scenarios, triggers)
- ❓ **WHY** use it vs alternatives (positioning in workflow)
- ❓ **HOW** it fits in workflows (before/after patterns)
- ❓ **DOMAIN KNOWLEDGE** (timing values, common patterns, gotchas, constraints)

**Parameter Descriptions Should Answer:**
- ❓ **WHAT** this parameter controls
- ❓ **FORMAT** expected (enum values, patterns, validation rules)
- ❓ **CONSTRAINTS** (min/max, required combinations)

**❌ AVOID in Tool Descriptions:**
- Repeating parameter purposes ("Use parameter X with value Y")
- Repeating return shape ("Returns {field1, field2}")
- Instructions on HOW to use a parameter (let param description handle it)
- Naming other specific tools (stay tool-agnostic for flexibility)

**✅ DO INCLUDE in Tool Descriptions:**
- Domain-specific knowledge (timing requirements, state patterns)
- Workflow context (call in parallel with..., use before/after...)
- Edge cases and gotchas the agent should know
- Use case examples (when this is appropriate)

### Tool Decoupling & Flexibility

**Avoid Cross-Tool References:**
- ❌ BAD: "Use with play_plex_media tool"
- ✅ GOOD: "Pass ratingKey to playback tool"
- ❌ BAD: "Call search_devices AND search_spotify in parallel"
- ✅ GOOD: "Call multiple lookups in parallel when independent"

**Why:** Tools may be disabled, renamed, or refactored. Generic guidance survives changes.

**Keep Descriptions General:**
- Support varied use cases and workflows
- Don't assume specific sequences or tool combinations
- Agent should compose tools flexibly, not follow prescriptive scripts

### System Prompt Principles

**Tool-Agnostic Guidance:**
- Generic parallelism rules, not specific tool pairings
- General workflow patterns (2-loop: gather → execute)
- Framework for decision-making, not specific sequences

**No Tool-Specific Tips:**
- Move service-specific search tips to tool descriptions
- Example: "Spotify search tips" → belongs in `search_spotify` description
- Keeps system prompt stable when tools are added/removed

**Minimize Reflection Overhead:**
- Avoid heavy "reflect twice, brainstorm, score" requirements
- Simple retry logic: "If fails, attempt one improved call"
- Reduces latency and over-thinking

### Duplication Detection Checklist

Before adding to a tool description, check:

1. **Is this explaining a parameter?** → Move to parameter description
2. **Is this naming another tool?** → Make generic or remove
3. **Is this repeating return shape?** → Already in `returns` field
4. **Is this in the system prompt?** → Remove one or make distinct
5. **Does this add unique value?** → Keep if YES (domain knowledge, timing, patterns)

### Examples of Good Tool Descriptions

**✅ GOOD - Workflow Context + Domain Knowledge:**
```python
description=(
    "Call Home Assistant service to control devices. Returns before/after state. "
    "TIMING GUIDE: Power on: 8-10s, App switching: 3-4s, Lights: 1-2s. "
    "If verification fails but command should work, follow up after delay."
)
```

**✅ GOOD - When/Why + Constraints:**
```python
description=(
    "Ask user for clarification to disambiguate requests. "
    "Use when you need details on rooms, devices, preferences, or ambiguous commands."
)
```

**❌ BAD - Repeating Parameters:**
```python
description=(
    "Search devices. Use area parameter to filter by room. "  # ← area param already says this
    "Use domain parameter for device type. "  # ← domain param already says this
    "Set k parameter for result count."  # ← k param already says this
)
```

**✅ BETTER - Use Cases + Context:**
```python
description=(
    "Find entities by semantic search. "
    "Check device summary before searching - if type isn't listed, it doesn't exist. "
    "WEATHER: Use domain='weather' and read 'temperature' attribute for current temp."
)
```

### Parameter Description Best Practices

**✅ GOOD - Format + Constraints:**
```python
"question": {
    "description": "Natural language question to ask user (use friendly names only, NO entity IDs)"
}
```

**✅ GOOD - Purpose + Validation:**
```python
"verify_after_seconds": {
    "description": "Wait N seconds then verify state changed (recommended: 3-5s for media, 1-2s for lights)"
}
```

**❌ BAD - Restating Field Name:**
```python
"query": {
    "description": "The query to search for"  # ← Obvious from name
}
```

**✅ BETTER - Context + Format:**
```python
"query": {
    "description": "Text query to search for matching devices"
}
```

### Maintenance

When modifying tools:
1. Check for duplication between description and params
2. Ensure no cross-tool references (use generic terms)
3. Verify description adds domain knowledge, not param echo
4. Test that tool works when other tools are disabled
5. Keep descriptions concise - every word adds latency
