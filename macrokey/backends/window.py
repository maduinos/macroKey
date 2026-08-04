"""Active window title, used for application-aware layer switching.

Best effort by design: when no backend works the title is ``None`` and the app
simply keeps the layer it already has.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import sys


def _x11_title() -> str | None:
    if shutil.which("xdotool") is None:
        return None
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["xdotool", "getactivewindow", "getwindowname"],
        capture_output=True,
        check=False,
        timeout=1.0,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip() or None


def _windows_title() -> str | None:
    try:
        import win32gui
    except ModuleNotFoundError:
        return None
    return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or None


def _macos_title() -> str | None:
    if shutil.which("osascript") is None:
        return None
    script = 'tell application "System Events" to get name of first process whose frontmost is true'
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["osascript", "-e", script], capture_output=True, check=False, timeout=1.0
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip() or None


@functools.lru_cache(maxsize=1)
def _selected():
    if sys.platform.startswith("win"):
        return "win32gui", _windows_title
    if sys.platform == "darwin":
        return "osascript", _macos_title
    if sys.platform.startswith("linux"):
        # Wayland compositors deliberately do not expose the focused window to
        # unprivileged clients, so X11/Xwayland is the only portable route.
        return "xdotool", _x11_title
    return "none", lambda: None


def window_backend_name() -> str:
    return _selected()[0]


def active_window_title() -> str | None:
    try:
        return _selected()[1]()
    except (OSError, subprocess.SubprocessError):
        return None
