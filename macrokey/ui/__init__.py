"""PySide6 view. The only package that imports a UI toolkit."""

__all__ = ["MissingToolkit", "run_gui"]

MISSING_QT = (
    "The editor window needs PySide6, which is an optional dependency:\n"
    '    python3 -m pip install --user "PySide6>=6.6"\n'
    "Every other command works without it -- try `macrokey --help`."
)


class MissingToolkit(RuntimeError):
    """PySide6 is not importable, so there is no window to open."""


def run_gui(port: str = "") -> int:
    try:
        from .app import run_gui as _run_gui
    except ImportError as exc:  # PySide6 absent, or a broken Qt install
        raise MissingToolkit(MISSING_QT) from exc

    return _run_gui(port=port)
