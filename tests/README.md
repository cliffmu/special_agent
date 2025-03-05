# Special Agent Tests

This directory contains unit tests for the Special Agent Home Assistant custom component.

## Testing Home Assistant Custom Components

Since Special Agent is a Home Assistant custom component, it has dependencies on the Home Assistant environment. There are two main approaches to testing:

### 1. Environment Setup Approach

This approach runs tests in an environment that simulates Home Assistant:

```bash
# Create a test environment (one-time setup)
mkdir -p ~/homeassistant_test
cd ~/homeassistant_test

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install Home Assistant core
pip install homeassistant

# Install test dependencies
pip install pytest pytest-asyncio

# Clone or copy the component to custom_components
mkdir -p custom_components
cp -r /path/to/special_agent custom_components/

# Run tests
cd custom_components/special_agent
python -m pytest tests/
```

### 2. Isolated Testing Approach (Current Implementation)

Our tests currently use mocking to avoid direct Home Assistant dependencies, letting us test functions in isolation:

```bash
# From the component directory:
pip install pytest pytest-asyncio
python -m pytest tests/
```

## Mocking Strategy

The tests use unittest.mock to replace Home Assistant dependencies:

- We mock `hass` objects
- We patch API calls
- We simulate responses

This approach lets us verify our component logic without a full Home Assistant installation.

## Running Tests

```bash
# Install required packages
pip install pytest pytest-asyncio

# Run all tests
python -m pytest tests/

# Run a specific test file
python -m pytest tests/test_agent_logic.py

# Run tests with more details
python -m pytest -v tests/
```

## Test Files

- `test_agent_logic.py` - Tests for the main orchestrator logic
- `test_command_history.py` - Tests for command history logging
- `test_entity_refinement.py` - Tests for entity filtering and ranking
- `test_gpt_commands.py` - Tests for LLM interaction functions

## Integration Testing

For full integration tests, consider:

1. Using [Home Assistant Developer Tools](https://developers.home-assistant.io/docs/development_testing) 
2. Testing in a development Home Assistant instance
3. Using a CI/CD pipeline with the [Home Assistant Github Actions](https://developers.home-assistant.io/docs/ci_actions)