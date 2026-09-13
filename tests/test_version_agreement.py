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

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
FIRMWARE_CONFIG = ROOT / "firmware" / "src" / "Config.h"


def packaged_version() -> str:
    match = re.search(r'^version = "(.+)"$', PYPROJECT.read_text("utf-8"), re.MULTILINE)
    assert match is not None, "pyproject.toml has no top-level version line"
    return match.group(1)


def test_version_matches_packaging_metadata() -> None:
    assert macrokey.__version__ == packaged_version()


def readme_versions() -> tuple[str, str]:
    """The line the README opens with: firmware first, then the app."""
    match = re.search(
        r"^버전: 펌웨어 `(.+?)` / 앱 `(.+?)`$", README.read_text("utf-8"), re.MULTILINE
    )
    assert match is not None, "README has no version line"
    return match.group(1), match.group(2)


def firmware_version() -> str:
    match = re.search(
        r'^#define MK_FIRMWARE_VERSION "(.+)"$',
        FIRMWARE_CONFIG.read_text("utf-8"),
        re.MULTILINE,
    )
    assert match is not None, "Config.h has no MK_FIRMWARE_VERSION"
    return match.group(1)


def test_the_readme_says_the_version_that_shipped() -> None:
    """A fourth copy of the number, and the one a user actually reads.

    It is the first line of the README and it drifted anyway: 0.18.0 went out
    while the README still said firmware 0.9.6 / app 0.17.1. Nothing else in
    the release depends on it, which is exactly why nobody notices -- the only
    thing it can do is tell a user they are running something they are not,
    and that matters most while they are reporting a bug against it.
    """
    firmware, app = readme_versions()
    assert app == macrokey.__version__
    assert firmware == firmware_version()
