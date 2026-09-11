"""Install only the bridge runtime from a verified, immutable project archive."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
from pathlib import Path
import tarfile
from urllib.request import urlopen

REVISION = "e6bc40109fcd99a6ccc3c2edeab0d5aa552ed26f"
ARCHIVE_SHA256 = "64218f4dd36ee2550eef75fed671d23e8222d0abd4e3b3ccfeebeed410ea70ac"
SOURCE_URL = f"https://codeload.github.com/cliffmu/special_agent/tar.gz/{REVISION}"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
RUNTIME_FILES = (
    "experimental/__init__.py",
    "experimental/live/__init__.py",
    "experimental/live/audio.py",
    "experimental/live/backend.py",
    "experimental/live/device_server.py",
    "experimental/live/requirements.txt",
    "experimental/live/server.py",
    "experimental/live/session.py",
)


def install_archive(archive: bytes, destination: Path) -> None:
    """Verify everything before writing; never extract archive paths or links."""
    if len(archive) > MAX_ARCHIVE_BYTES or hashlib.sha256(archive).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("Bridge source archive failed SHA-256 verification")
    files = {}
    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as source:
        for relative_path in RUNTIME_FILES:
            member = source.getmember(f"special_agent-{REVISION}/{relative_path}")
            if not member.isfile() or member.size > 1024 * 1024:
                raise ValueError("Unexpected bridge source archive member")
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError("Missing bridge source archive member")
            with stream:
                files[relative_path] = stream.read()
    for relative_path, content in files.items():
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    with urlopen(SOURCE_URL, timeout=60) as response:
        archive = response.read(MAX_ARCHIVE_BYTES + 1)
    install_archive(archive, args.destination)


if __name__ == "__main__":
    main()
