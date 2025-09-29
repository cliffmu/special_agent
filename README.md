# Special Agent

This is an early scaffold of the Special Agent Home Assistant integration.

The project is gradually migrating to an agent-based architecture. See
[`migration_to_agent_plan.md`](migration_to_agent_plan.md) for the complete
roadmap.

Install by copying this repository into your `custom_components/special_agent`
folder and restart Home Assistant. Use the `special_agent.reload` service to
reload the integration after making changes.

## Debug logging
Go to Settings \u25b8 Devices & Services \u25b8 Special\u00a0Agent \u25b8 3-dot menu \u25b8 Enable debug logging.
Switch off to return to normal (INFO) logging.

## Monitoring the Agent

The Special Agent logs detailed information about its operations. Here's what you can monitor:

### Enable Debug Logging
1. Go to **Settings** \u25b8 **Devices & Services** \u25b8 **Special Agent**
2. Click the **3-dot menu** \u25b8 **Enable debug logging**
3. View logs: **Settings** \u25b8 **System** \u25b8 **Logs** (filter by `custom_components.special_agent`)

### What Gets Logged
The agent logs:
- **System Prompt**: The full system prompt sent to LLM
- **User Prompt**: User's input text
- **Tools Provided**: Available tools and their schemas
- **AI Response Content**: LLM's thinking and decisions
- **AI Tool Selection**: Which tools the LLM chooses to call
- **Tool Inputs/Results**: What data is sent to tools and what they return
- **Final Messages**: Complete conversation history

### Quick Monitoring Services
Call these services to check agent status:

```yaml
# Get current sessions
service: special_agent.get_sessions

# Get last interaction details
service: special_agent.get_last_interaction
```

### Add Agent Status Sensor
Add this to your `configuration.yaml` to create a sensor showing agent activity:

```yaml
sensor:
  - platform: template
    sensors:
      special_agent_status:
        friendly_name: "Special Agent Status"
        value_template: >-
          {% set state = states('special_agent.sessions') %}
          {% if state %}
            {% set sessions = state_attr('special_agent.sessions', 'sessions') | default({}) %}
            {{ sessions | length }} active session{{ 's' if sessions | length != 1 else '' }}
          {% else %}
            No sessions
          {% endif %}
        attribute_templates:
          sessions: "{{ state_attr('special_agent.sessions', 'sessions') }}"
```

### Real-time Monitoring
During conversations, you can:
1. **Watch the logs** in real-time during agent operation
2. **Call the monitoring services** after each user utterance
3. **Check the sensor** for active session count
4. **Monitor conversation IDs** to track specific interactions

## Vector index utilities
`utils/vector_index.py` includes helpers to build and query a simple NumPy-based
index of Home Assistant entities. `async_load_vector_index` asynchronously loads
the saved index using a background thread (or Home Assistant's executor when
available).
