"""Install only the bridge runtime from a verified, immutable project archive."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
from pathlib import Path
import tarfile
from urllib.request import urlopen

REVISION = "32bee3eae834595c21abae93f2d29d4b7e19d3dc"
ARCHIVE_SHA256 = "d4bfe093063d5a7a8f6228c7e78aa9d4cd7e25b1bd5bfbbb27309d34c84553e0"
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
