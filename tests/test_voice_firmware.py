"""Firmware preparation integrity and the Voice PE/bridge configuration contract."""

import hashlib
from io import BytesIO
from pathlib import Path
import re

import pytest

from experimental.live.firmware import prepare


FIRMWARE = Path(__file__).resolve().parents[1] / "experimental/live/firmware"
UPSTREAM = b"""external_components:
  - source:
      type: git
      url: https://github.com/esphome/home-assistant-voice-pe
      ref: dev
    components: [voice_kit]
    refresh: 0s
  - source:
      type: git
      url: https://github.com/TristanBrotherton/voicepe-realtime-firmware
      ref: main
    components: [va_client]
    refresh: 0s
microphone:
  sample_rate: 16000
  bits_per_sample: 32bit
  channel: stereo
"""


def test_download_checks_integrity_before_source_can_be_used(monkeypatch):
    name, expected = "LICENSE", b"original upstream license"
    monkeypatch.setitem(prepare.FILES, name, hashlib.sha256(expected).hexdigest())
    requests = []

    def serve(content):
        def response(request, timeout):
            requests.append((request.full_url, timeout))
            return BytesIO(content)
        monkeypatch.setattr(prepare, "urlopen", response)

    serve(expected)
    assert prepare.verified_download(name) == expected
    assert requests == [(prepare.SOURCE + "/LICENSE", 30)]
    assert re.fullmatch(r"[0-9a-f]{40}", prepare.REVISION)
    assert re.fullmatch(r"[0-9a-f]{40}", prepare.VOICE_KIT_REVISION)
    serve(expected + b" changed after pinning")
    with pytest.raises(ValueError, match="SHA-256"):
        prepare.verified_download(name)


def test_download_rejects_oversized_source_even_with_matching_digest(monkeypatch):
    oversized = b"x" * 512_001
    monkeypatch.setitem(prepare.FILES, "LICENSE", hashlib.sha256(oversized).hexdigest())
    monkeypatch.setattr(prepare, "urlopen", lambda *args, **kwargs: BytesIO(oversized))
    with pytest.raises(ValueError, match="SHA-256"):
        prepare.verified_download("LICENSE")


def test_prepared_package_pins_both_components_and_preserves_hardware_configuration():
    result = prepare.pin_components(UPSTREAM).decode()
    assert "ref: " + prepare.REVISION in result
    assert "ref: " + prepare.VOICE_KIT_REVISION in result
    assert "ref: main\n" not in result and "ref: dev\n" not in result
    assert "refresh: 0s" not in result
    assert result.split("microphone:\n", 1)[1] == UPSTREAM.decode().split("microphone:\n", 1)[1]
    assert "UPSTREAM-LICENSE" in result


@pytest.mark.parametrize("source", [
    UPSTREAM.replace(b"ref: dev", b"ref: changed"),
    UPSTREAM + b"      ref: main\n",
])
def test_unexpected_upstream_layout_fails_instead_of_silently_leaving_moving_refs(source):
    with pytest.raises(ValueError, match="exactly one"):
        prepare.pin_components(source)


def test_prepare_materializes_source_and_license_without_requiring_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(prepare, "verified_download", lambda name: UPSTREAM if name.endswith("yaml") else b"license")
    monkeypatch.setattr("sys.argv", ["prepare.py", "--output", str(tmp_path)])
    prepare.main()
    assert (tmp_path / "UPSTREAM-LICENSE").read_bytes() == b"license"
    assert prepare.REVISION in (tmp_path / "voice-hardware.yaml").read_text()
    assert not (tmp_path / "secrets.yaml").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_wrapper_preserves_continuous_audio_secret_transport_and_physical_mute():
    # These are intentional configuration contracts, also validated with a full
    # ESPHome compile. Keep this lightweight check independent of the build SDK.
    config = (FIRMWARE / "voice-pe.yaml").read_text()
    assert re.search(r"(?m)^  barge_in: true$", config)
    assert re.search(r"(?m)^  va_url: !secret special_agent_voice_url$", config)
    assert re.search(r"(?m)^  api_key: !secret api_key$", config)
    assert re.search(r"(?m)^  level: WARN$", config)  # Upstream INFO logs contain the token URL.
    assert "voice_hardware: !include .generated/voice-hardware.yaml" in config
    assert re.search(r"(?m)^  - id: !extend master_mute_switch\n    on_turn_on:\n      - lambda: id\(va\)->send_interrupt\(\);$", config)
    assert "VOICE_DEVICE_TOKEN" in (FIRMWARE / "secrets.example.yaml").read_text()
    assert "/voice?token=" in (FIRMWARE / "secrets.example.yaml").read_text()
    ignored = (FIRMWARE / ".gitignore").read_text().splitlines()
    assert "secrets.yaml" in ignored and ".generated/" in ignored


def test_listening_override_disables_only_local_stop_detection():
    # ESPHome config validation additionally checks this appends an eighth action
    # after all seven upstream LED/wake/idle actions; it must not replace them.
    config = (FIRMWARE / "voice-pe.yaml").read_text()
    client = re.search(r"(?ms)^va_client:\n(.*?)(?=^[a-z_]+:)", config).group(1)
    assert re.search(
        r"(?m)^  on_phase:\n    - if:\n        condition:\n"
        r"          lambda: 'return phase == \"listening\";'\n"
        r"        then:\n          - micro_wake_word.disable_model: stop$",
        client,
    )
    assert "!override" not in client
    assert "micro_wake_word.stop" not in client  # Its engine also owns the microphone.
    assert "send_interrupt" not in client
    assert "on_wake_word_detected:" not in config  # Preserve upstream wake/button handling.
