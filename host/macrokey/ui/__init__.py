"""Tkinter view. The only package that imports a UI toolkit."""

__all__ = ["run_gui"]


def run_gui(port: str = "") -> int:
    from .app import run_gui as _run_gui

    return _run_gui(port=port)
