"""``main.py`` is the only entry point a downloaded release has.

``build_release.sh`` hands PyInstaller ``main.py``, not the ``macrokey`` console
script from ``pyproject.toml``. The launcher used to call ``run_gui()`` and never
look at ``sys.argv``, so on a release binary ``--version`` opened the window and
printed nothing -- the version fix in e548410 landed somewhere no user could
reach. These tests bind the shipped entry point to the CLI so it cannot quietly
come apart again.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import macrokey
from macrokey import runtime

ROOT = Path(__file__).resolve().parent.parent


def test_entry_script_prints_version() -> None:
    """What the release binary is asked for most, run the way it is shipped."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--version"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == macrokey.__version__


def test_entry_script_delegates_to_the_cli(monkeypatch) -> None:
    """No arguments still opens the window, but through the CLI's dispatch."""
    import main
    from macrokey import cli

    seen: list[list[str] | None] = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(argv) or 7)
    monkeypatch.setattr(sys, "argv", ["macrokey"])

    assert main.main() == 7
    assert seen == [None]


def test_windowless_build_gets_writable_streams(monkeypatch) -> None:
    """A ``--windowed`` Windows exe starts with ``sys.stdout`` set to None.

    Printing must not raise there; without a console to attach to the output has
    nowhere to go, but the process still has to survive saying so.
    """
    monkeypatch.setattr(runtime, "frozen", lambda: True)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    runtime.ensure_stdio()

    assert sys.stdout is not None and sys.stderr is not None
    print("this must not raise")
    sys.stdout.close()
    sys.stderr.close()
