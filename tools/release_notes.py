#!/usr/bin/env python3
"""Print CHANGELOG.md's section for one version, for a release body.

The release workflow used to write a body that listed the files it had just
uploaded and nothing else. That is fine on the releases page, where the files
are the point, and useless everywhere the body is read as *what changed* --
including the app, which downloads and installs a release on its own and then
has to tell someone what it did.

    python3 tools/release_notes.py 0.14.0

Nothing on stdout and a non-zero exit when the version has no section, which is
the workflow's cue to fall back rather than publish an empty release note.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def section(text: str, version: str) -> str:
    """The body under `## <version> — …`, up to the next `##` heading.

    Matched on the version alone rather than the whole heading: the date after
    it is written by hand and a release cut a day later than its entry says
    would otherwise silently publish no notes at all.
    """
    heading = re.compile(rf"^## {re.escape(version)}(?:\s|$)", re.MULTILINE)
    start = heading.search(text)
    if start is None:
        return ""
    # Past the rest of the heading line -- the "— 2026-09-04" after the
    # version belongs to the heading, not to the notes.
    line_end = text.find("\n", start.end())
    rest = text[line_end + 1 :] if line_end != -1 else ""
    end = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: end.start()].strip() if end else rest.strip()


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <version>", file=sys.stderr)
        return 2
    body = section(CHANGELOG.read_text("utf-8"), sys.argv[1])
    if not body:
        print(f"no CHANGELOG section for {sys.argv[1]}", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
