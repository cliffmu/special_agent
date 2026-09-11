# Special Agent

This is an early scaffold of the Special Agent Home Assistant integration.

The project is gradually migrating to an agent-based architecture. See
[`migration_to_agent_plan.md`](docs/migration_to_agent_plan.md) for the complete
roadmap.

Install by copying this repository into your `custom_components/special_agent`
folder and restart Home Assistant. Use the `special_agent.reload` service to
reload the integration after making changes.

## Debug logging
Go to Settings \u25b8 Devices & Services \u25b8 Special\u00a0Agent \u25b8 3-dot menu \u25b8 Enable debug logging.
Switch off to return to normal (INFO) logging.

## Vector index utilities
`utils/vector_index.py` includes helpers to build and query a simple NumPy-based
index of Home Assistant entities. `async_load_vector_index` asynchronously loads
the saved index using a background thread (or Home Assistant's executor when
available).

Plex issue fix Close session in Plex on AppleTV. Force close Plex on AppleTV. Open Plex on AppleTV. Skip login/register. Activate "Announce as player". Login again and check "Announce as Player" is active.

---

DEV Commit Target (Not release)
action: update.install
target:
  entity_id: update.special_agent_update
data:
  version: dev

## GPT-Live browser experiment

The `codex/gpt-live-test` branch adds an opt-in browser test for `gpt-live-1`.
It keeps the existing Special Agent Responses model and tools in Home Assistant.
The browser sends microphone audio directly to GPT-Live over WebRTC; a local
Python server attaches to the same session and runs delegated requests through
HA's authenticated Conversation API. Audio continues while the backend works.
This is a browser prototype, not firmware for Home Assistant Voice devices.

### Start with the demo backend

Use Python 3.11 or later in a separate terminal on the computer with your browser:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r experimental/live/requirements.txt
export OPENAI_API_KEY="<your OpenAI project API key>"
.venv/bin/python -m experimental.live.server
```

Open **http://localhost:8099** and choose **Start conversation**. The OpenAI
project needs access to `gpt-live-1`. No API call or microphone recording begins
just by opening the page. API keys stay in the server environment.

Try: “Run the demo task.” While it takes five seconds, ask “Tell me a joke.”
The task result should arrive while the same conversation stays open. The demo
does not control devices or perform information lookups; only the voice session
uses OpenAI. The page displays transcripts, task status, timings, and final usage.

### Connect the existing Special Agent

First install this branch into `custom_components/special_agent` and reload or
restart HA, as above. Check that the normal Special Agent conversation works.
Then restart the local test server with:

```sh
export HA_URL="http://homeassistant.local:8123"
export HA_TOKEN="<Home Assistant long-lived access token>"
export HA_AGENT_ID="conversation.special_agent"
.venv/bin/python -m experimental.live.server --backend home-assistant
```

Use the actual Special Agent entity ID from your HA installation if it differs.
The server calls `POST /api/conversation/process` and retains HA's returned
conversation ID for follow-ups and confirmation questions. HA keeps its existing
model, enabled tools, and confirmation setting. **This mode can operate real
devices through those tools.** The browser has no satellite/room identity, so
name the room explicitly, for example “Which lights are on in the kitchen?”
Browser requests omit `device_id` and do not start HA satellite TTS.

For a useful first comparison, try a state lookup, a command that asks for
confirmation, and a longer media setup followed by a joke. Check actual device
state as well as the spoken result. A greeting or simple joke can stay in the
live frontend; requests requiring current information or home tools delegate to
Special Agent. A separate lightweight information backend is not implemented.

### Lifecycle and current limits

- The server binds to `127.0.0.1`, checks Host/Origin, and accepts one active
  browser session. Use localhost, not a public deployment or LAN-facing bind.
- Idle sessions close after 90 seconds without transcript activity and pending
  work; the maximum is ten minutes. Adjust with `--idle-timeout` and
  `--max-duration` (seconds). GPT-Live bills session duration and backend API
  usage separately. End conversations when finished.
- **End conversation closes audio; it does not undo an accepted HA action.**
  Queued work is skipped, and an in-flight HA request may finish. The page keeps
  polling to show its result. Network failures are not automatically retried,
  because a device action may have succeeded before the response was lost.
  An uncertain HA outcome also stops queued follow-ups and closes the voice
  session; check the device before starting another request.
- HA jobs are serialized. You can continue speaking while one runs, but a new
  home request or correction waits for the active request. The bridge does not
  provide mid-sequence cancellation inside HA. Older results become quiet
  context when a newer delegation is waiting; all results remain visible.
- Delegation events contain no request text. The bridge gathers transcript
  fragments, waits briefly for late fragments, and passes recent context plus
  the current request to HA. This debounce is not an authoritative turn boundary;
  it never launches work without a delegation event. Ambiguous or incomplete
  requests should result in clarification. Test names, corrections, and short
  confirmation replies carefully before relying on this for regular use.
- The backend attaches before the browser receives the SDP answer, so it can
  observe conversation events from the start. Sideband attachment does not
  replay earlier events and does not require a second `session.start`.
- Normal close waits for `session.closed` and records its cumulative usage.
  A timeout or lost connection leaves final usage unconfirmed. The REST hangup
  fallback is best effort; its API reference specifically documents SIP.
- Audio is not saved by this harness. Recent transcripts/results stay in its
  memory, and existing HA logging/session persistence still applies.
- Voice PE firmware, microphone/speaker transport, and acoustic echo cancellation
  still need their own integration and hardware tests. Changing a model dropdown
  is not sufficient. Browser WebRTC handles capture/playback and interruptions.

### Other improvements on this branch

- Reuse loaded tools for unchanged configuration, including simultaneous first
  requests. Options changes replace the agent without mutating an active request.
- Stop context-overflow recovery after two retries. History trimming removes
  whole older user turns, preserving function calls with their results.
- Skip satellite-pipeline continuation when a text/browser request has no device.

### Verification

```sh
.venv/bin/python -m pip install pytest pytest-asyncio numpy
.venv/bin/python -m pytest tests/test_live_bridge.py -q
node --test tests/live_browser.cjs
```

The tests exercise mocked Live sessions and local fake HTTP/WebSocket endpoints;
they make no paid OpenAI requests and do not operate HA devices. Additional
agent lifecycle regressions are in `tests/test_agent_request_lifecycle.py`.
Existing tests expect the repository import name `special_agent`; when using a
directory named `special-agent`, provide a parent import alias for that package.
The original dev suite has stale tests importing removed APIs; those failures
also reproduce on the unmodified dev branch.

The implementation has **not yet been verified against an authenticated
GPT-Live session or physical HA hardware**. Use the demo and the checks above
before switching a voice device.

Protocol references:
[GPT-Live WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc?api=live),
[server-side controls](https://developers.openai.com/api/docs/guides/voice-server-controls?api=live),
[client delegation](https://developers.openai.com/api/docs/guides/live-delegation),
[session lifecycle](https://developers.openai.com/api/docs/guides/live-conversations),
[Home Assistant Conversation API](https://developers.home-assistant.io/docs/intent_conversation_api/).
