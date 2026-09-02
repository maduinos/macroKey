#!/usr/bin/env python3
"""Launcher for the macroKey binaries built by ``build_release.sh``.

PyInstaller bundles this file rather than the ``macrokey`` console script, so
anything this ignores does not exist for someone who downloaded a release. It
used to call ``run_gui()`` without ever reading ``sys.argv``, which left the
whole CLI -- ``--version``, ``ports``, ``info``, ``push``, ``pull``, ``record``
-- reachable only from a source checkout, and made ``--version`` on a release
binary silently open the window instead. Delegate instead; with no arguments
``cli.main`` opens the same window it always did.
"""

from __future__ import annotations

from macrokey import cli
from macrokey.runtime import ensure_stdio


def main() -> int:
    ensure_stdio()
    return cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
