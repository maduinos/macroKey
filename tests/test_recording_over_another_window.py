"""Whose clicks the editor is allowed to swallow.

Recording is started from the pad, not from this window, so the person then
works in whatever application the macro is for -- and that application is
usually maximised, which puts it *over* the editor. The filter that stops a
click on the editor's own buttons becoming a macro step used to be a screen
rectangle, which knows only coordinates, so it was throwing away clicks that
had gone to the application in front.

A macro recorded over a maximised Excel came back with the pointer moving from
cell to cell and never clicking one. Gating the rectangle on this window being
the active one was not enough: the recording that finally settled it was made
with the editor sitting behind Excel -- where that gate should have switched
the rectangle off -- and three of its seven clicks were still missing, all
three inside the editor's coordinates and every surviving one outside them.

So there is no rectangle any more. Qt delivering the press to a widget in this
process is the evidence, and a click that went to the window in front never
produces one.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey.recorder.events import MOUSE_CLICK, MOUSE_RELEASE, RawEvent  # noqa: E402
from macrokey.ui.app import MainWindow  # noqa: E402


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    return MainWindow()


def press_and_release(recorder, at: float, token: str = "left") -> None:
    recorder._events.append(RawEvent(kind=MOUSE_CLICK, token=token, at=at))
    recorder._events.append(RawEvent(kind=MOUSE_RELEASE, token=token, at=at + 0.08))


def test_a_click_on_the_application_in_front_stays_in_the_macro(window) -> None:
    """The whole bug, end to end: nothing here ever hears about that click."""
    recorder = window.app.recorder
    recorder.recording = True
    recorder._events.clear()
    recorder._editor_click_ats.clear()

    press_and_release(recorder, at=100.0)
    recorder._drop_editor_clicks()

    assert [event.kind for event in recorder._events] == [MOUSE_CLICK, MOUSE_RELEASE]


def test_a_click_this_window_received_is_not_a_macro_step(window) -> None:
    recorder = window.app.recorder
    recorder.recording = True
    recorder._events.clear()
    recorder._editor_click_ats.clear()

    press_and_release(recorder, at=100.0)
    recorder._editor_click_ats.append(100.02)  # Qt delivers it a moment later
    recorder._drop_editor_clicks()

    assert recorder._events == []


def test_the_release_leaves_with_its_press(window) -> None:
    """An orphaned release is worse than a stray one: normalize skips it as the
    tail of a drag that began before capture, so the pair half-vanishes."""
    recorder = window.app.recorder
    recorder.recording = True
    recorder._events.clear()
    recorder._editor_click_ats.clear()

    press_and_release(recorder, at=100.0)
    press_and_release(recorder, at=101.0)
    recorder._editor_click_ats.append(100.02)
    recorder._drop_editor_clicks()

    kinds = [(event.kind, event.at) for event in recorder._events]
    assert kinds == [(MOUSE_CLICK, 101.0), (MOUSE_RELEASE, 101.08)]


def test_the_next_click_elsewhere_is_not_swallowed_too(window) -> None:
    """The match is ordered. Capture sees a click before Qt does, so only a
    click that came *before* the window's own event can be that event -- and
    clicking this window then immediately clicking the macro's target is
    exactly how a recording gets going."""
    recorder = window.app.recorder
    recorder.recording = True
    recorder._events.clear()
    recorder._editor_click_ats.clear()

    recorder._editor_click_ats.append(100.0)
    press_and_release(recorder, at=100.1)  # in the app in front, just after
    recorder._drop_editor_clicks()

    assert [event.kind for event in recorder._events] == [MOUSE_CLICK, MOUSE_RELEASE]


def test_nothing_is_noted_while_no_recording_runs(window) -> None:
    recorder = window.app.recorder
    recorder.recording = False
    recorder._editor_click_ats.clear()

    recorder.note_editor_click()

    assert recorder._editor_click_ats == []


def test_a_real_press_on_this_window_reaches_the_recorder(window) -> None:
    """The wiring, not the rule: an actual Qt press must be noted."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    recorder = window.app.recorder
    recorder.recording = True
    recorder._editor_click_ats.clear()

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(1.0, 1.0),
        QPointF(1.0, 1.0),
        QPointF(1.0, 1.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.MouseEventSource.MouseEventNotSynthesized,
    )
    QApplication.instance().sendEvent(window, press)

    assert len(recorder._editor_click_ats) == 1


def test_only_one_watcher_ever_filters_the_application(window) -> None:
    """An application-wide filter runs for every event in the process, so one
    per window would cost a Python call per event per window ever made."""
    from macrokey.ui.app import _EditorClickWatch

    MainWindow()
    MainWindow()

    watchers = QApplication.instance().findChildren(_EditorClickWatch)
    assert len(watchers) == 1
