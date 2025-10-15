Details for the migration from prototype to new AI agentic version are in migration_to_agent_plan.md. Look at that file first as it details the project, features and development roadmap.

I have a prototype version of the project saved in REFERENCE folder. This folder is just for reference and should not be edited. Look at the REFERENCE folder for examples of how certain code worked but I'm working on migrating this project to an AI agent based tool. Do not use code in the REFERENCE folder to replicate it unless its needed, im trying to improve off the prototype so I dont want to carry issues over to new version. 

### Plex setup flexibility

- **Context**: Plex environments vary widely (server names, library structures, entity domains). Avoid baking in assumptions that break across installations.
- **Entity handling**: Plex controls may surface as different Home Assistant domains (e.g., `media_player`, `button`). We added an entity exception to allow Plex buttons when appropriate. Prefer capability checks over strict domain checks where possible.
- **Configuration**: Make Plex-related entities and identifiers configurable. Support multiple Plex servers and heterogeneous device mappings.
- **Action**: When adding new Plex tools or logic, ensure they work with both players and button-style entities, and document any required configuration.

---

## Code Organization Principles

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
- Human-readable flow: load tools → build prompt → execute loop → return
- **Move complex logic to `utils/`** - session management, response parsing, performance tracking
- Already refactored: `utils/session_helpers.py`, `utils/response_utils.py`, `utils/performance.py`

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