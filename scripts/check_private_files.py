"""Reject ignored/private files in the Git index without reading their contents."""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import subprocess
import sys

PRIVATE_EXPORTS = {
    "docs/agent_session_play_plex.json",
    "docs/scene_memory_example.json",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Check only added or modified staged paths")
    args = parser.parse_args()
    command = (
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
        if args.staged else ["git", "ls-files", "-z"]
    )
    paths = subprocess.check_output(command)
    if not paths:
        return 0
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        input=paths, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if ignored.returncode not in (0, 1):
        print("Could not verify ignore rules; commit blocked.", file=sys.stderr)
        return 1
    blocked = set(ignored.stdout.split(b"\0")) - {b""}
    for raw in paths.split(b"\0"):
        if not raw:
            continue
        path = PurePosixPath(raw.decode("utf-8", errors="surrogateescape"))
        if path.as_posix() in PRIVATE_EXPORTS or "__pycache__" in path.parts:
            blocked.add(raw)
    if blocked:
        print("Commit contains private or ignored files. Remove them from the index before continuing:", file=sys.stderr)
        for path in sorted(blocked):
            print("  " + repr(path.decode("utf-8", errors="replace")), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
