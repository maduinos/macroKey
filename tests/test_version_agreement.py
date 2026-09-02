"""``pyproject.toml`` and ``macrokey.__version__`` are two copies of one number.

The packaging metadata is what the release workflow tags and names a build with
(``.github/workflows/release.yml``); ``__version__`` is what the window title and
``--version`` show. Nothing links them, and they had already drifted a release
apart -- 0.10.0 shipped with a window saying v0.9.0 -- because a disagreement
fails nowhere. It just tells the user the wrong thing about what they are running,
which matters most when they are reporting a bug against it.

Read with a regex rather than ``tomllib``: the package targets 3.10, where that
module does not exist, and the release workflow reads the same line with ``sed``.
"""

from __future__ import annotations

import re
from pathlib import Path

import macrokey

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def packaged_version() -> str:
    match = re.search(r'^version = "(.+)"$', PYPROJECT.read_text("utf-8"), re.MULTILINE)
    assert match is not None, "pyproject.toml has no top-level version line"
    return match.group(1)


def test_version_matches_packaging_metadata() -> None:
    assert macrokey.__version__ == packaged_version()
