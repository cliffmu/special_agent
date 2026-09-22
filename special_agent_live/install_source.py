"""Install only the bridge runtime from a verified, immutable project archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
from io import BytesIO
from pathlib import Path
import tarfile
from urllib.request import urlopen

REVISION = "e075c21d922fabf93df13a183c137cfb6a8bca22"
# Verify the immutable tar bytes; gzip encoding can vary across archive servers.
SOURCE_TAR_SHA256 = "9fc4396a07ae72549bff71ed2646789ddb1ab8f24cd62aa8e007a1db31758db5"
SOURCE_URL = f"https://codeload.github.com/cliffmu/special_agent/tar.gz/{REVISION}"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_TAR_BYTES = 32 * 1024 * 1024
RUNTIME_FILES = (
    "experimental/__init__.py",
    "experimental/live/__init__.py",
    "experimental/live/audio.py",
    "experimental/live/backend.py",
    "experimental/live/device_server.py",
    "experimental/live/devices.py",
    "experimental/live/requirements.txt",
    "experimental/live/server.py",
    "experimental/live/session.py",
    "utils/__init__.py",
    "utils/constants.py",
    "utils/log_presentation.py",
    "utils/logging.py",
)


def install_archive(archive: bytes, destination: Path) -> None:
    """Verify everything before writing; never extract archive paths or links."""
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise ValueError("Bridge source archive exceeds size limit")
    try:
        with gzip.GzipFile(fileobj=BytesIO(archive)) as compressed:
            source_tar = compressed.read(MAX_SOURCE_TAR_BYTES + 1)
    except (OSError, EOFError) as error:
        raise ValueError("Bridge source archive is not valid gzip") from error
    if len(source_tar) > MAX_SOURCE_TAR_BYTES:
        raise ValueError("Bridge source tar exceeds size limit")
    if hashlib.sha256(source_tar).hexdigest() != SOURCE_TAR_SHA256:
        raise ValueError("Bridge source archive failed SHA-256 verification")
    files = {}
    with tarfile.open(fileobj=BytesIO(source_tar), mode="r:") as source:
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
