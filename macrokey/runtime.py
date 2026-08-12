"""Runtime helpers for PyInstaller builds vs editable installs."""

from __future__ import annotations

import sys


def frozen() -> bool:
    """True when running from a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")
