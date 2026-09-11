"""Materialize a verified, pinned Voice PE package without vendoring its sources.

Run once before ESPHome config/compile. This only downloads public source files;
it never reads secrets, contacts a device, or flashes firmware.
"""

import argparse
import hashlib
from pathlib import Path
from urllib.request import Request, urlopen


REVISION = "cf73d8dcee605a774229554e946f1fc51e515b2e"
VOICE_KIT_REVISION = "0579e7b9d8504264719c593474c85447253c9dc1"
SOURCE = "https://raw.githubusercontent.com/TristanBrotherton/voicepe-realtime-firmware/" + REVISION
FILES = {
    "home-assistant-voice.realtime.yaml": "389e9d8b5c26755c6e778635ad2b4800ced4693b257408a9ea9549945e5114e7",
    "LICENSE": "1f312390122725ee382f85af00e3df84f2490cd8fa61b9ea172bc6591cc0ac63",
}


def verified_download(name):
    request = Request(SOURCE + "/" + name, headers={"User-Agent": "special-agent-firmware-prepare"})
    with urlopen(request, timeout=30) as response:
        data = response.read(512_001)
    if len(data) > 512_000 or hashlib.sha256(data).hexdigest() != FILES[name]:
        raise ValueError("Upstream " + name + " did not match the pinned SHA-256 digest")
    return data


def pin_components(data):
    """Only replace the two floating external-component refs in verified YAML."""
    text = data.decode("utf-8")
    for old, revision in (("dev", VOICE_KIT_REVISION), ("main", REVISION)):
        marker = "      ref: " + old + "\n"
        if text.count(marker) != 1:
            raise ValueError("Expected exactly one upstream component ref: " + old)
        text = text.replace(marker, "      ref: " + revision + "\n")
    # Immutable git revisions do not need a fetch on every compile.
    text = text.replace("    refresh: 0s\n", "    refresh: never\n")
    notice = (
        "# Prepared by Special Agent: external component refs pinned on 2026-09-10.\n"
        "# Original source: " + SOURCE + "/home-assistant-voice.realtime.yaml\n"
        "# See UPSTREAM-LICENSE in this directory. All original notices follow.\n"
    )
    return (notice + text).encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / ".generated")
    args = parser.parse_args()
    source = verified_download("home-assistant-voice.realtime.yaml")
    license_text = verified_download("LICENSE")
    generated = pin_components(source)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in (("voice-hardware.yaml", generated), ("UPSTREAM-LICENSE", license_text)):
        temporary = args.output / (name + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(args.output / name)
    print("Prepared pinned Voice PE package at " + str(args.output / "voice-hardware.yaml"))


if __name__ == "__main__":
    main()
