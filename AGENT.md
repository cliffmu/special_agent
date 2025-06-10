# Claude Instructions for Special Agent Project

## Project Overview
Special Agent is a Home Assistant custom component that uses LLMs to process natural language commands for home automation.

## Code Organization Guidelines

### Keep agent_logic.py Simple
- **Key Principle**: Keep `agent_logic.py` especially the `process_conversation_input` function simple and readable.
- **Purpose**: This file serves as the orchestrator/controller that ties all components together.
- **Implementation**: New functionality should generally go in specialized files, not in this central orchestrator.

### Logical Organization by File Purpose
- Add new code to existing files based on their logical categories:
- Follow the naming pattern to maintain clarity and organization

## File Structure and Responsibilities

| File | Purpose | Function |
| ---- | ------- | -------- |
| `agent_logic.py` | Main orchestrator | Primary flow control, session management |
| `vector_index.py` | Vector database functionality | Build, query and manage embeddings index |
| `data_sources.py` | Data retrieval | Get Home Assistant states and entities, execute commands |
| `entity_refinement.py` | Entity processing | Filter, rank, and organize entities |
| `gpt_commands.py` | LLM interactions | All OpenAI/Claude API calls and prompts |
| `conversation.py` | Home Assistant integration | Conversation agent implementation |
| `spotify_integration.py` | Music services | Spotify search and playback |
| `logger_helper.py` | Logging | Consistent logging functionality |
| `command_history.py` | Command logging | Track execution history and results |

## Common Commands

### Testing
```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_agent_logic.py

# Test with verbose output
pytest -v tests/
```

### Linting
```bash
# Lint all Python files
pylint **/*.py

# Fix common issues
autopep8 --in-place --aggressive --aggressive **/*.py
```

### Building
```bash
# Install in development mode
pip install -e .

# Build package
python setup.py sdist bdist_wheel
```

## How Components Connect
1. `conversation.py` receives user input from Home Assistant
2. `agent_logic.py` orchestrates the flow by:
   - Classifying intent via `gpt_commands.py`
   - Finding relevant devices via `vector_index.py`
   - Refining entities via `entity_refinement.py`
   - Generating commands via `gpt_commands.py`
   - Executing via `data_sources.py`
   - Tracking history via `command_history.py`

## Code Style Preferences
- Keep functions focused on a single responsibility
- Add detailed docstrings for all functions
- Use type hints where helpful for clarity
- Prefer explicit over implicit
- Log important state changes and decisions
- Handle errors gracefully with informative messages

## Important Notes
- When implementing new features, carefully consider session management implications
- Test all changes with multiple devices to ensure multi-device sessions work correctly
- Consider voice interaction UX - keep responses concise and clear
- Ensure all code additions include appropriate error handling