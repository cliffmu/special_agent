# Special Agent

This is an early scaffold of the Special Agent Home Assistant integration.

The project is gradually migrating to an agent-based architecture. See
[`migration_to_agent_plan.md`](docs/migration_to_agent_plan.md) for the complete
roadmap.

Install by copying this repository into your `custom_components/special_agent`
folder and restart Home Assistant. Use the `special_agent.reload` service to
reload the integration after making changes.

## Agent model and activity logs

For Voice PE, open **Settings → Apps → Special Agent Live → Configuration** to
select **Agent model**, **Thinking effort**, and **Fast mode**. Save and restart
the app to apply changes. These controls require integration **0.3.2+** and Live
app **0.1.5+**. They apply to this bridge's delegated requests; the integration's
other conversations retain their own settings. New app setups default to
**gpt-5.6-terra / low / Fast off**. Legacy options without these fields inherit
the integration until the new controls are saved.

The integration's defaults remain available at **Settings → Devices & services →
Special Agent → Configure**. Existing installations keep their saved model.
Terra is a balanced starting point for tool use; try Luna for lower cost and
compare accuracy on your routines. Sol and Astra are options for harder requests.
A newer small model is not automatically more reliable on every task.
[Model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-terra).

Fast mode explicitly requests OpenAI Fast processing; off explicitly requests the
standard tier, even if the API project enables Fast. Fast currently costs **2×**
standard token rates for these four models. It can shorten model processing, but
cannot speed up an external device or service call. Logs show the requested and
actual tier (5.6 may report `priority` for Fast, or `default` if downgraded).
[Fast mode](https://developers.openai.com/api/docs/guides/fast-mode).
This setting affects delegated Python-agent work; Live speech still uses
`gpt-live-1`. Unsupported reasoning choices use `low` for that model, and logs
show the effective choice. In the integration's Configure dialog, leave secret
fields blank to keep existing keys. Preserve the saved key/token in app options.

Concise activity logs are on by default:

- **Consolidated:** Settings → Apps → Special Agent Live → **Log** shows voice
  sessions/jobs plus Python request IDs, model/tier, token counts, tool start/end,
  verification, timing, failures and completion. Python activity arrives while
  tools are running, usually within a second. This requires the home-assistant
  backend. A bounded in-memory buffer recovers recent records after reconnect;
  it is not a permanent log archive.
- **Core copy:** Settings → System → Logs → Home Assistant Core → menu →
  **Show raw logs**, then filter `special_agent.activity`. Core still retains
  these records for troubleshooting when the bridge is unavailable.

These activity records omit prompts, transcripts, raw tool arguments/results,
and credentials. Optional detailed performance CSV and integration debug logging
are separate diagnostics and may contain request content. Enable debug logging
from the Special Agent integration menu only when investigating a specific issue.

Special Agent's learned routines live in `scene_memory.json` under its
`sa_vector_index` persistence directory, separate from Home Assistant `scenes.yaml`.
`.storage/.special_agent_sessions.json` contains conversation/tool history, not
native HA scene definitions. A successful service-call result confirms the API
call completed; Home Assistant state readback is a separate check and still
reflects integration telemetry rather than independent physical proof.

Direct controls and scene service steps share mandatory verification in Python.
They submit the command once, then compare supported state/settings against HA
readback within a bounded deadline. Already-matching settings verify immediately.
Lights get a minimum five-second verification budget, even if a model asks for a
shorter wait (longer waits and transitions are respected). The overall scene
deadline still caps that budget.
After an observed light-setting mismatch, a matching readback must remain stable
for half a second. A deadline reached during settling stays unverified. Results
include both HA's raw 0–255 `brightness` and the derived `brightness_pct` with
explicit units, so raw 40 cannot be mistaken for 40%.
Results separate `accepted` from `verification: verified / failed / unverified`;
unsupported commands or missing telemetry stay unverified. The agent cannot skip
this check by omitting a tool argument. Saved explicit post-conditions are retained
and checked in addition to the automatic checks. No verification failure
automatically repeats a device command.

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

### Install the integration branch through HACS

The **Redownload → Need a different version?** dropdown lists releases; a test
branch may not appear there. In Home Assistant, open **Settings → Tools → Actions**
(called **Developer Tools → Actions** on older versions), choose YAML mode, and run:

```yaml
action: update.install
target:
  entity_id: update.special_agent_update
data:
  version: codex/gpt-live-test
```

If HACS says that branch version is already downloaded, use the full 40-character
commit SHA shown on the GitHub test branch as `version` instead. This selects an
exact build and avoids reinstalling a cached branch label.

Restart Home Assistant after the download. This installs the integration changes;
it does not flash the Voice device or launch the audio bridge. Use your actual
HACS update entity ID if it differs. HACS documents branch and full-commit installs
through its [update action](https://www.hacs.dev/docs/use/entities/update/).

### Run the bridge as a Home Assistant app/add-on

On Home Assistant OS/Supervised, the bridge can run beside Core. In **Settings →
Apps → Install app → ⋮ → Repositories** (older versions: **Add-ons → Add-on Store**),
add this repository URL, including the branch suffix:

```text
https://github.com/cliffmu/special_agent#codex/gpt-live-test
```

Refresh the store and select **Special Agent Live**, then install it. Its
first installation builds a container from a pinned, hash-verified bridge source
revision. The app repository and the HACS integration use the same GitHub project,
but are installed and updated independently.

In the app's Configuration tab, set `api_key` to the OpenAI API key, `device_token`
to a random URL-safe token of at least 32 characters, `room` to the room name,
and `agent_id` to your active Special Agent conversation entity ID.
Check **Tools → States** for the entity: an older restored
`conversation.special_agent` can be unavailable while the active entity has a
suffix such as `conversation.special_agent_2`.

Start with the demo backend. Keep the network mapping **8099/tcp → 8099** and start
the app. Enable **Start on boot** after the first successful test. The firmware
URL is `ws://HA_HOST:8099/voice?token=YOUR_DEVICE_TOKEN`, using your HA host's LAN
IP, not its port 8123 or a cloud/remote-access URL. `/health` on port 8099 reports
the bridge and device connection state. Do not expose this port to the internet.

After device audio works, switch the app backend to `home-assistant` and restart
it. The app uses Supervisor's Core API proxy and its app token; you do not need
to create or paste a separate long-lived HA access token. Its Home Assistant API
permission lets delegated requests use the configured Special Agent tools.

**Keep listening after activity (seconds)** controls the follow-up window and
defaults to **30**. The session stays open while the agent speaks or has pending
work, then gives you the full window after the latest recognized speech, audible
reply, or completed task. Speaking again restarts the countdown. Save and restart
the app after changing the value. Set **Optional session length limit (seconds)**
to **0** (the default) to avoid a cutoff measured from the initial wake; a positive
value explicitly enables that separate limit. Existing installations keep their
saved values, so change them to `30` and `0` when updating.

Continue with the firmware preparation below. HACS installation and app
installation do not change the Voice PE firmware automatically.

### 1. Run the LAN bridge

Skip this section when using the Home Assistant app above. For a separate LAN
machine, use Python 3.11 or later on an always-on machine reachable by the Voice device.
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
and retain the device's encrypted ESPHome integration in Home Assistant. The
voice WebSocket connects independently of HA API pairing; a wake/button event
can start a Live session before pairing completes.

Factory firmware can have no configured API encryption key. In that case, use a
new random 32-byte Base64 key for this firmware, then enter it in the existing
ESPHome integration when HA requests an encryption key after installation.

These credentials serve different connections:

| Credential | Where it belongs |
| --- | --- |
| OpenAI API key | The app's `api_key` option; authenticates GPT-Live access. |
| ESPHome API encryption key | Firmware `secrets.yaml` → `api_key` and HA's ESPHome pairing dialog. |
| Device token | The app's `device_token` option and the firmware's compiled bridge URL. |

Paste credential values into HA fields **without surrounding quotes**. Editing
saved pairing JSON alone does not change the flashed device. To change the
device token, update the app option and rebuild/reflash the Voice PE with the
matching token in its bridge URL.

When identifying the USB device, compare its **custom** MAC with the sticker and
HA device page. ESPHome uses that address; `esptool read-mac` instead reports the
chip's factory MAC, which can differ. The read-only command
`espefuse --chip esp32s3 --port YOUR_USB_PORT get-custom-mac` reports the address
used by the Voice PE on your network.

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

This firmware can also update the Voice PE's XMOS audio processor to its bundled
firmware (1.3.1). An ESP32 flash backup does **not** include the separate XMOS
firmware. Restoring stock behavior requires reflashing firmware; the factory-reset
button only clears preferences. The
[official Voice PE installer](https://esphome.github.io/home-assistant-voice-pe/)
provides a USB recovery path.

The preparation script downloads hash-verified community YAML and its license
from a pinned revision, then pins its external components. The community YAML
and Python portions are MIT licensed; its C++ audio client is GPLv3. Original
sources and notices remain in `.generated/` and at the linked upstream revision.

### 3. Test with the actual device

For the first audio test, press the center button, wait for the chime and about
one second, then say **“Say hello in one short sentence.”** Stop with the center
button or physical mute. Once that works, say **“Okay Nabu”** or press the button
and ask “Run the demo task.” While it works, ask “Tell me a joke.” Both speech
input and playback remain active during the backend task.
The bridge retains up to two seconds of startup microphone audio and paces it
to Live after session creation, so normal connection setup does not lose the
first words. A slow connection or stalled audio ends the attempt instead of
accumulating a delayed recording; wake it again once connectivity is restored.

Saying **“stop”** stops the current spoken response while the session keeps
listening, so the ring can remain animated. The center button or physical mute
closes the Live session; the idle and total-duration limits below also end it.
The local stop-word detector is disabled during Live conversation so ordinary
speech cannot falsely trigger its end-session command. Spoken corrections go
directly to GPT-Live.

For hands-free startup, use **“Okay Nabu”**, wait for the wake chime, then speak.
Under **Settings → Devices & services → ESPHome → your Voice PE →
Configuration → Wake word**, choose the active dropdown. This firmware includes
**Okay Nabu**, **Hey Jarvis**, and **Hey Leonard**; the selection is saved on the
device and does not require rebuilding. An unavailable wake-word field left over
from stock firmware is not the active selector. Wake detection runs on the Voice
PE using microWakeWord, so the openWakeWord app is not used for this Live path.
An arbitrary typed phrase is not enough: a custom phrase needs a compatible
[trained wake-word model](https://esphome.io/components/micro_wake_word/#model-json)
and a firmware rebuild. The app's idle setting and the device's wake-word selector
are independent; you do not need another wake word during an open conversation.

Ordinary spoken interruptions are heard by Live while it is talking. Full duplex
is experimental: test at low speaker volume first. The upstream firmware normally
disables this mode because acoustic echo can leak through the Voice PE's XMOS
processing. Software validation cannot establish how well interruption and echo
cancellation work in your room.

### 4. Connect your Special Agent tools

If using the Home Assistant app, select its `home-assistant` backend and restart
the app as described above. The environment commands below are only for a
standalone bridge on another machine.

Install this branch into `custom_components/special_agent` and restart HA. Check
that a normal Special Agent conversation works, then restart the bridge with:

```sh
export HA_URL="http://homeassistant.local:8123"
export HA_TOKEN="<Home Assistant long-lived access token>"
export HA_AGENT_ID="conversation.special_agent"
.venv/bin/python -m experimental.live.device_server --backend home-assistant
```

Use your actual Special Agent conversation entity ID and integration 0.3.2+.
The standalone bridge inherits HA's configured model; the app's model controls
override it per request. HA retains enabled tools and confirmation rules. The
authenticated `/api/special_agent/live/process` endpoint accepts only registered
Special Agent entities. The bridge passes `VOICE_ROOM` as
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
- Idle sessions close after 30 seconds following the latest recognized speech,
  audible playback, or completed backend work. Pending work prevents idle closure;
  microphone silence and silent output packets do not keep it awake. Speech timing
  uses received transcript fragments, so this is an approximate inactivity window.
  Configure `--idle-timeout` in seconds; `--max-duration 0` disables the optional
  duration cap (the default). API and transport limits still apply. GPT-Live bills
  session duration separately from the backend model.
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

Hardware testing has covered GPT-Live microphone/speaker audio, interruptions,
spoken stop, the center button, Okay Nabu wake, the idle countdown, and delegated
tool work during conversation. Broader timeout and echo testing across rooms and
volumes remains useful. Automated tests cover model overrides, activity-log
forwarding and simulated light transitions; device telemetry still requires
checking against the actual installation. The earlier browser diagnostic remains
in `experimental/live/server.py`; it is not required for the Voice PE setup.

The full Voice PE firmware compiled successfully with ESPHome 2026.8.2 and
ESP-IDF 5.5.5. Rebuild with your own device settings before installing it.

References: [Live WebSocket audio](https://developers.openai.com/api/docs/guides/voice-websockets?api=live),
[Live playback controls](https://developers.openai.com/api/docs/guides/voice-server-controls?api=live),
[client delegation](https://developers.openai.com/api/docs/guides/live-delegation),
[Home Assistant Conversation API](https://developers.home-assistant.io/docs/intent_conversation_api/),
[pinned Voice PE firmware](https://github.com/TristanBrotherton/voicepe-realtime-firmware/tree/cf73d8dcee605a774229554e946f1fc51e515b2e).
