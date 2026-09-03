"""Whose clicks the editor window's rectangle is allowed to swallow.

Recording is started from the pad, not from this window, so the person then
works in whatever application the macro is for -- and that application is
usually maximised, which puts it *over* the editor. The rectangle that stops a
click on the editor's own buttons becoming a macro step knows only coordinates,
so it was throwing away clicks that had gone to the application in front.

A macro recorded over a maximised Excel came back with the pointer moving from
cell to cell and never clicking one: the moves survived (they stopped being
filtered by this rectangle in 0.12.0) and every click inside the editor's
coordinates did not. Windows only -- the rectangle is a pynput fallback, and
evdev never consults it, which is why the same recording made on Linux was fine.

Being active is what tells the two cases apart: clicking this window makes it
the active one, and working in the application in front does not.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey.ui.app import MainWindow  # noqa: E402


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    return MainWindow()


def test_the_rectangle_is_dropped_while_another_window_is_in_front(window, monkeypatch) -> None:
    """The recorder must not filter clicks that went to the app in front."""
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    window.app.recorder.ignore_click_region = (0, 0, 1920, 1080)

    window._sync_ignored_region()

    assert window.app.recorder.ignore_click_region is None


def test_the_rectangle_is_this_window_while_it_is_the_active_one(window, monkeypatch) -> None:
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    window.app.recorder.ignore_click_region = None

    window._sync_ignored_region()

    frame = window.frameGeometry()
    assert window.app.recorder.ignore_click_region == (
        frame.x(),
        frame.y(),
        frame.width(),
        frame.height(),
    )


def test_a_click_on_the_application_in_front_becomes_a_macro_step(window, monkeypatch) -> None:
    """The whole bug, end to end: the click reaches the recorder's event list.

    The coordinates are inside the editor's rectangle deliberately -- that is
    the case that used to be dropped.
    """
    from macrokey.recorder.events import MOUSE_CLICK

    recorder = window.app.recorder
    recorder.capture_mouse = True
    recorder._events.clear()

    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    window._sync_ignored_region()

    frame = window.frameGeometry()
    inside = (frame.x() + frame.width() // 2, frame.y() + frame.height() // 2)
    recorder._on_click(inside[0], inside[1], type("B", (), {"name": "left"}), True)

    assert [event.kind for event in recorder._events] == [MOUSE_CLICK]
