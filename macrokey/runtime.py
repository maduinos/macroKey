"""Runtime helpers for PyInstaller builds vs editable installs."""

from __future__ import annotations

import os
import sys


def frozen() -> bool:
    """True when running from a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def ensure_stdio() -> None:
    """Give a ``--windowed`` build somewhere to print.

    ``build_release.sh`` builds both binaries with ``--windowed``. On Linux that
    only means no terminal is opened for the app and the inherited stdio keeps
    working. On Windows it makes the exe a GUI-subsystem program: it is handed
    no console at all, so ``sys.stdout`` and ``sys.stderr`` are ``None`` and the
    first thing the CLI prints -- a version, a port listing, an error -- raises
    ``AttributeError`` instead of reaching anyone.

    Attaching to the console of whoever launched us puts that output back in
    their cmd/PowerShell window. Double-clicked from Explorer there is no such
    console to attach to; the streams then go to devnull so printing stays
    harmless, which costs nothing because that path opens the GUI anyway.
    """
    if not frozen():
        return
    if sys.platform == "win32" and (sys.stdout is None or sys.stderr is None):
        _attach_parent_console()
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _attach_parent_console() -> None:
    """Adopt the console we were launched from, and reopen stdio onto it."""
    import ctypes

    attach_parent_process = -1
    try:
        attached = ctypes.windll.kernel32.AttachConsole(attach_parent_process)
    except (AttributeError, OSError):  # not Windows after all, or no kernel32
        return
    if not attached:  # launched with no console of its own to borrow
        return
    for name in ("stdout", "stderr"):
        try:
            stream = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
        except OSError:
            continue
        setattr(sys, name, stream)
