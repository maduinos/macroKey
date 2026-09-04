"""The one-key editor, which is now only about the shortcut the key sends.

Mouse actions and the recording switches used to sit under the shortcut field.
They were settings for other features wearing the shape of a binding -- the
recording ones were the same two checkboxes the main window already has -- and
the tests here hold that window to the one question it asks.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QDialog,
    QLabel,
    QPushButton,
)

from macrokey.app import MacroKeyApp  # noqa: E402
from macrokey.ui.slot_dialog import SlotDialog  # noqa: E402


@pytest.fixture
def app(monkeypatch, tmp_path):
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    instance = MacroKeyApp()
    yield instance
    instance.close()


@pytest.fixture
def dialog(app):
    """A dialog whose grab never touches a real keyboard.

    Every test here drives `KeyGrab`'s signals by hand. Left alone, `start`
    would open and *exclusively grab* the input devices of whoever is running
    the suite, which is a genuinely bad thing for a test to do.
    """
    window = SlotDialog(None, app, 0, "tap")
    window.key_grab.start = lambda: (False, "not in this test")
    yield window
    window.close()


# ------------------------------------------------------------- what is gone --


def test_the_mouse_actions_are_not_offered_here(dialog) -> None:
    labels = [button.text() for button in dialog.findChildren(QPushButton)]
    assert "Left click" not in labels
    assert "Wheel up" not in labels


def test_the_recording_switches_are_not_offered_here(dialog) -> None:
    """They live in the main window, next to the log that shows what they do."""
    assert dialog.findChildren(QCheckBox) == []


def test_clear_names_only_the_binding_it_will_clear(app) -> None:
    window = SlotDialog(None, app, 2, "double")
    labels = [button.text() for button in window.findChildren(QPushButton)]
    assert "Clear double binding" in labels
    assert "Clear this key" not in labels
    window.close()


# ---------------------------------------------------------- reading the keys --


def test_a_combination_read_from_the_keyboard_fills_the_field(dialog) -> None:
    dialog.key_grab.captured.emit("ctrl+alt+shift+f13")
    assert dialog.shortcut.text() == "ctrl+alt+shift+f13"
    dialog._use_shortcut()
    assert dialog.result_action.kind == "key"
    assert dialog.result_action.hotkey == "ctrl+alt+shift+f13"


def test_a_key_the_pad_cannot_send_is_reported_rather_than_silently_kept(dialog) -> None:
    """Reading the kernel means reading keys with no keycode to send: a laptop
    Fn, a media key on a build without the consumer page, a hangeul key."""
    dialog.key_grab.captured.emit("ctrl+fn")
    assert "fn" in dialog.hint.text()
    assert dialog.shortcut.text() == "ctrl+fn"


def test_falling_back_to_this_window_says_why(dialog) -> None:
    dialog._press_keys()
    assert dialog.shortcut.capturing is True
    assert "not in this test" in dialog.hint.text()


def listens(dialog, *, exclusive: bool) -> None:
    """Makes `start` succeed the way the real one does, minus the devices."""

    def start() -> tuple[bool, str]:
        dialog.key_grab.active = True
        dialog.key_grab.exclusive = exclusive
        return True, ""

    dialog.key_grab.start = start


def test_listening_says_whether_the_desktop_sees_the_keys_too(dialog) -> None:
    listens(dialog, exclusive=True)
    dialog._press_keys()
    alone = dialog.hint.text()
    assert dialog.press_keys.text() == "Stop"

    dialog.key_grab.stop()
    listens(dialog, exclusive=False)
    dialog._press_keys()
    assert dialog.hint.text() != alone


def test_stopping_without_a_key_puts_the_previous_shortcut_back(dialog) -> None:
    dialog.shortcut.setText("ctrl+alt+shift+1")
    listens(dialog, exclusive=True)
    dialog._press_keys()
    assert dialog.shortcut.text() == ""

    dialog.key_grab.stop()
    assert dialog.shortcut.text() == "ctrl+alt+shift+1"
    assert dialog.press_keys.text() == "Press keys"


def test_closing_the_window_gives_the_keyboard_back(dialog) -> None:
    """The grab holds the keyboard away from the desktop, so it must not
    outlive the window -- including the paths that are not the Cancel button."""
    stopped: list[str] = []
    dialog.key_grab.stop = lambda reason="": stopped.append(reason)
    dialog.done(QDialog.Rejected)
    assert stopped == [""]


def test_the_status_line_reserves_room_for_every_sentence_it_can_show(dialog) -> None:
    from PySide6.QtCore import QRect, Qt

    from macrokey.ui.slot_dialog import _HINT_WRAP_WIDTH

    for text in dialog.possible_hints():
        needed = dialog.hint.fontMetrics().boundingRect(
            QRect(0, 0, _HINT_WRAP_WIDTH, 0), Qt.TextWordWrap, text
        )
        assert dialog.hint.minimumHeight() >= needed.height(), text


def test_the_window_still_shows_what_the_key_does_now(dialog) -> None:
    assert any(
        label.text().startswith("Now:") for label in dialog.findChildren(QLabel)
    )


def test_the_keyboard_is_left_to_a_recording_that_is_already_running(dialog, app) -> None:
    """An exclusive grab would take the events away from it, and the macro
    would lose exactly what was typed while this window was listening."""
    taken: list[str] = []
    dialog.key_grab.start = lambda: taken.append("grabbed") or (True, "")
    app.recorder.recording = True
    try:
        dialog._press_keys()
    finally:
        app.recorder.recording = False
    assert taken == []
    assert dialog.shortcut.capturing is True
    assert "recording" in dialog.hint.text()
