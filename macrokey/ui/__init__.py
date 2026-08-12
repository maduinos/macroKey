"""PySide6 view. The only package that imports a UI toolkit."""

from __future__ import annotations

__all__ = ["MissingToolkit", "run_gui"]

MISSING_QT = (
    "The editor window needs PySide6.\n"
    "  • Preferred: ./build_release.sh → releases/linux/macrokey\n"
    "  • Developers: python -m pip install -r requirements.txt && python main.py"
)

MISSING_QT_FROZEN = (
    "This macroKey build was packaged without the editor toolkit. "
    "Rebuild with ./build_release.sh."
)


class MissingToolkit(RuntimeError):
    """PySide6 is not importable, so there is no window to open."""


def run_gui(port: str = "") -> int:
    try:
        from .app import run_gui as _run_gui
    except ImportError as exc:  # PySide6 absent, or a broken Qt install
        from ..runtime import frozen

        raise MissingToolkit(MISSING_QT_FROZEN if frozen() else MISSING_QT) from exc

    return _run_gui(port=port)
