"""Capturing a shortcut by pressing it.

The trap is shift. Pressing ctrl+alt+shift+1 produces the character "!", and
taking that as the key gives ctrl+alt+shift+! -- a combination the keypad has no
key for and this app cannot parse. A shortcut is modifiers plus a physical key;
what the glyph becomes is the receiving application's business.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey.config import Action  # noqa: E402
from macrokey.ui.widgets import ShortcutEdit  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def field(qt_app):
    widget = ShortcutEdit()
    widget.start_capture()
    return widget


def press(widget, key, modifiers=Qt.NoModifier, text=""):
    widget.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, key, modifiers, text))


ALL_THREE = Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier


@pytest.mark.parametrize(
    "key, text, expected",
    [
        (Qt.Key_1, "!", "ctrl+shift+alt+1"),
        (Qt.Key_2, "@", "ctrl+shift+alt+2"),
        (Qt.Key_8, "*", "ctrl+shift+alt+8"),
    ],
    ids=["1 not !", "2 not @", "8 not *"],
)
def test_shift_does_not_replace_the_key_with_its_glyph(field, key, text, expected) -> None:
    press(field, key, ALL_THREE, text)
    assert field.text() == expected


def test_every_captured_hyper_shortcut_encodes(field) -> None:
    """The default binding for all eight keys, so it has to survive capture."""
    for digit in range(1, 9):
        field.start_capture()
        press(field, getattr(Qt, f"Key_{digit}"), ALL_THREE, "!@#$%^&*"[digit - 1])
        Action(kind="key", hotkey=field.text()).encode()


def test_a_plain_letter_is_captured_unshifted(field) -> None:
    press(field, Qt.Key_A, Qt.ShiftModifier, "A")
    assert field.text() == "shift+a"


def test_function_keys_are_named(field) -> None:
    press(field, Qt.Key_F5)
    assert field.text() == "f5"


@pytest.mark.parametrize(
    "key, expected",
    [(Qt.Key_Escape, "esc"), (Qt.Key_Return, "enter"), (Qt.Key_Space, "space")],
)
def test_named_keys_use_this_projects_vocabulary(field, key, expected) -> None:
    press(field, key)
    assert field.text() == expected


def test_a_modifier_on_its_own_is_not_a_shortcut(field) -> None:
    press(field, Qt.Key_Control, Qt.ControlModifier)
    assert not field.text().rstrip("+")


def test_capture_ends_after_one_combination(field) -> None:
    """Otherwise adjusting it by hand afterwards would be swallowed."""
    press(field, Qt.Key_F5)
    assert field.capturing is False


def test_typing_works_when_not_capturing(qt_app) -> None:
    """Some shortcuts cannot be pressed here, so they must stay typeable."""
    widget = ShortcutEdit()
    assert widget.capturing is False
    widget.setText("ctrl+alt+shift+9")
    assert widget.text() == "ctrl+alt+shift+9"
    Action(kind="key", hotkey=widget.text()).encode()


def test_capture_replaces_rather_than_appends(field) -> None:
    press(field, Qt.Key_F5)
    field.start_capture()
    press(field, Qt.Key_F6)
    assert field.text() == "f6"
