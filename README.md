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

## GPT-Live on Home Assistant Voice

The `codex/gpt-live-test` branch adds an experimental **Home Assistant Voice
Preview Edition** audio path. The device streams microphone audio to a Python
bridge on your LAN; the bridge streams it to `gpt-live-1` and returns its audio to
the device speaker. Special Agent stays in HA and runs delegated requests through
its existing tools while the voice conversation continues.

This requires the custom ESPHome firmware below. Installing the HA integration
alone does not change the stock wake → STT → conversation → TTS pipeline. The
firmware is for the official Voice Preview Edition; other ESPHome voice devices
need a hardware-specific configuration.

### 1. Run the LAN bridge

Use Python 3.11 or later on an always-on machine reachable by the Voice device.
Run these commands from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r experimental/live/requirements.txt
export OPENAI_API_KEY="<OpenAI project key with gpt-live-1 access>"
export VOICE_DEVICE_TOKEN="<random URL-safe token of at least 32 characters>"
export VOICE_ROOM="Living room"
.venv/bin/python -m experimental.live.device_server
```

Generate the device token with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
Use that same token in the firmware's bridge URL. OpenAI and HA access keys stay
on the bridge; the device only receives its own bridge token.

The server listens on port **8099** on the LAN. `http://BRIDGE_IP:8099/health`
reports whether the device is connected and capturing audio. It accepts one
Voice device at a time. This transport is intended for a trusted LAN: device
WebSocket traffic is unencrypted, so do not expose the port to the internet.
Access logs are disabled because the firmware authenticates using a URL token.

The default backend is a five-second demonstration task. It cannot operate home
devices, but the voice session still uses the paid GPT-Live API. A session starts
only after a wake/button event, not merely when the device connects.

### 2. Prepare and install Voice PE firmware

Keep your existing ESPHome device configuration and its restore image. Match the
existing device name, API encryption key and OTA password in this configuration,
and retain the device's encrypted ESPHome integration in Home Assistant; the
upstream firmware starts its voice client after HA connects.

```sh
python3 experimental/live/firmware/prepare.py
cp experimental/live/firmware/secrets.example.yaml experimental/live/firmware/secrets.yaml
```

Edit `experimental/live/firmware/secrets.yaml` with your Wi-Fi and existing device
credentials. Set `special_agent_voice_url` to
`ws://BRIDGE_IP:8099/voice?token=YOUR_DEVICE_TOKEN`. Edit `name` and `friendly_name`
in `experimental/live/firmware/voice-pe.yaml`. Generated sources, local secrets
and firmware build files are ignored by Git.

Use a separate environment for the pinned ESPHome version:

```sh
python3 -m venv .venv-firmware
.venv-firmware/bin/python -m pip install esphome==2026.8.2
.venv-firmware/bin/esphome config experimental/live/firmware/voice-pe.yaml
.venv-firmware/bin/esphome compile experimental/live/firmware/voice-pe.yaml
```

The compile command produces an image; it does not flash the device. Install it
on your test Voice PE using ESPHome Device Builder's manual upload or your
existing ESPHome OTA workflow. Restore the previous firmware to return to stock
Assist. The configuration preserves the center button, physical mute, speaker
controls and encrypted native HA API.

The preparation script downloads hash-verified community YAML and its license
from a pinned revision, then pins its external components. The community YAML
and Python portions are MIT licensed; its C++ audio client is GPLv3. Original
sources and notices remain in `.generated/` and at the linked upstream revision.

### 3. Test with the actual device

Say **“Okay Nabu”** or press the center button. Wait for the listening indication
on your first tests, then say “Run the demo task.” While it works, ask “Tell me a
joke.” Both speech input and playback remain active during the backend task.
The bridge retains up to two seconds of startup microphone audio and paces it
to Live after session creation, so normal connection setup does not lose the
first words. A slow connection or stalled audio ends the attempt instead of
accumulating a delayed recording; wake it again once connectivity is restored.

The center button, mute control, or configured stop word closes the Live session.
Ordinary spoken interruptions are heard by Live while it is talking. Full duplex
is experimental: test at low speaker volume first. The upstream firmware normally
disables this mode because acoustic echo can leak through the Voice PE's XMOS
processing. Software validation cannot establish how well interruption and echo
cancellation work in your room.

### 4. Connect your Special Agent tools

Install this branch into `custom_components/special_agent` and restart HA. Check
that a normal Special Agent conversation works, then restart the bridge with:

```sh
export HA_URL="http://homeassistant.local:8123"
export HA_TOKEN="<Home Assistant long-lived access token>"
export HA_AGENT_ID="conversation.special_agent"
.venv/bin/python -m experimental.live.device_server --backend home-assistant
```

Use your actual Special Agent conversation entity ID. HA keeps its configured
model, enabled tools and confirmation rules. The bridge passes `VOICE_ROOM` as
context and retains HA's conversation ID for follow-ups. It omits `device_id` so
HA does not launch a second TTS response over the direct Live speaker stream.

Try a state lookup, an action requiring confirmation, and a longer media request
followed by a joke. Greetings and simple jokes can stay with Live. Requests for
current information, home state, tools or substantive reasoning delegate to
Special Agent. A separate lightweight information backend is not implemented.

### Session behavior and limits

- Home Assistant requests run in the background and are serialized. A new home
  request or correction waits for the action already running; ordinary voice
  conversation can continue. Stopping audio does not cancel or undo an accepted
  HA action. Queued actions are skipped when the session closes.
- Delegation events carry no request text. The bridge gathers transcript
  fragments and waits briefly for late fragments before calling HA. This is a
  heuristic, not an authoritative turn boundary; no action starts from silence
  detection alone. Test ambiguous names, corrections and short confirmations.
- There is no documented Live output-audio-done event. Microphone capture stays
  continuous throughout playback, and firmware phases do not change per utterance.
  Device input is PCM16 mono 16 kHz, resampled continuously to Live's negotiated 24 kHz;
  Live output passes directly to the firmware's 24 kHz speaker path.
- Idle sessions close after 90 seconds without transcript activity or pending
  work; total duration defaults to 10 minutes. Configure `--idle-timeout` and
  `--max-duration` in seconds. The firmware may stop earlier if it detects no
  speech. GPT-Live bills session duration separately from the backend model.
- Normal close waits for `session.closed` and logs cumulative usage. Lost
  connections or finalization timeouts leave final usage unconfirmed and stop
  automatic restart. No device action is automatically retried after an uncertain
  HA outcome. Check the actual device state before repeating such a request.
- The bridge does not save audio. Recent transcripts/results live in memory;
  existing HA session storage and logging still apply. After stopping audio, a
  running HA action can finish, but its result is no longer spoken on that session.

### Other improvements on this branch

- Reuse loaded tools when configuration is unchanged, including simultaneous first
  requests. Options changes replace the agent without mutating an active request.
- Cap context-overflow recovery at two retries. Trim whole older user turns,
  preserving function calls with their results.
- Skip satellite-pipeline continuation when a text/bridge request has no device.

### Verification

```sh
.venv/bin/python -m pip install pytest pytest-asyncio numpy
.venv/bin/python -m pytest tests/test_voice_device.py tests/test_live_audio.py tests/test_live_bridge.py tests/test_voice_firmware.py -q
```

These tests use fake Live and device sockets and make no paid API requests or
home actions. Agent regressions are in `tests/test_agent_request_lifecycle.py`.
Existing integration tests require a parent import alias named `special_agent`
when the checkout directory is named `special-agent`. The original dev suite also
contains stale tests importing removed APIs.

**A physical Voice PE and an authenticated GPT-Live session have not yet been
tested.** The configuration and mocked audio/delegation path can be validated
locally; actual speaker echo, interruptions, model access and latency require the
device test above. The earlier browser diagnostic remains in
`experimental/live/server.py`; it is not used by this hardware setup.

The full Voice PE firmware compiled successfully with ESPHome 2026.8.2 and
ESP-IDF 5.5.5 using placeholder credentials (39.3% application flash, 51.2%
static RAM). Rebuild with your real device settings before installing it.

References: [Live WebSocket audio](https://developers.openai.com/api/docs/guides/voice-websockets?api=live),
[Live playback controls](https://developers.openai.com/api/docs/guides/voice-server-controls?api=live),
[client delegation](https://developers.openai.com/api/docs/guides/live-delegation),
[Home Assistant Conversation API](https://developers.home-assistant.io/docs/intent_conversation_api/),
[pinned Voice PE firmware](https://github.com/TristanBrotherton/voicepe-realtime-firmware/tree/cf73d8dcee605a774229554e946f1fc51e515b2e).
