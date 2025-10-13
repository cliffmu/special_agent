Details for the migration from prototype to new AI agentic version are in migration_to_agent_plan.md. Look at that file first as it details the project, features and development roadmap.

I have a prototype version of the project saved in REFERENCE folder. This folder is just for reference and should not be edited. Look at the REFERENCE folder for examples of how certain code worked but I'm working on migrating this project to an AI agent based tool. Do not use code in the REFERENCE folder to replicate it unless its needed, im trying to improve off the prototype so I dont want to carry issues over to new version. 

### Plex setup flexibility

- **Context**: Plex environments vary widely (server names, library structures, entity domains). Avoid baking in assumptions that break across installations.
- **Entity handling**: Plex controls may surface as different Home Assistant domains (e.g., `media_player`, `button`). We added an entity exception to allow Plex buttons when appropriate. Prefer capability checks over strict domain checks where possible.
- **Configuration**: Make Plex-related entities and identifiers configurable. Support multiple Plex servers and heterogeneous device mappings.
- **Action**: When adding new Plex tools or logic, ensure they work with both players and button-style entities, and document any required configuration.