"""Offering to flatten pointer acceleration, and never doing it uninvited.

The pad sends relative movement, so the desktop decides how far that is on
screen. An adaptive profile makes that decision depend on speed. The firmware
replays at the recorded speed so the curve largely cancels, but flat removes
the variable -- which is worth offering and is not ours to change quietly: it
changes how someone's mouse feels everywhere, not only inside a macro.
"""

from __future__ import annotations

import pytest

from macrokey import capture_setup

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey.ui.app import MainWindow  # noqa: E402

# ------------------------------------------------------------- detection --


def test_the_profile_is_read_without_its_quotes(monkeypatch) -> None:
    monkeypatch.setattr(capture_setup, "_gsettings", lambda *a: "'flat'")
    assert capture_setup.pointer_accel_profile() == "flat"
    assert capture_setup.pointer_accel_is_flat()
    assert not capture_setup.pointer_accel_can_be_flattened()


def test_an_unreadable_setting_is_not_treated_as_flat(monkeypatch) -> None:
    """Windows, a non-GNOME desktop, no gsettings at all. Nothing to offer, and
    nothing to claim either -- 'unknown' must not read as 'already fine'."""
    monkeypatch.setattr(capture_setup, "_gsettings", lambda *a: None)

    assert capture_setup.pointer_accel_profile() is None
    assert not capture_setup.pointer_accel_is_flat()
    assert not capture_setup.pointer_accel_can_be_flattened()


def test_an_adaptive_profile_is_worth_offering_to_change(monkeypatch) -> None:
    monkeypatch.setattr(capture_setup, "_gsettings", lambda *a: "'default'")
    assert capture_setup.pointer_accel_can_be_flattened()


def test_a_change_that_did_not_take_is_reported_as_failure(monkeypatch) -> None:
    """gsettings exits 0 for a key it did not apply, so the write is read back."""
    monkeypatch.setattr(capture_setup, "_gsettings", lambda *a: "'default'")

    ok, message = capture_setup.set_pointer_accel_flat()

    assert not ok
    assert "did not take" in message


# ------------------------------------------------------------- the offer --


class FakeBox:
    """Stands in for QMessageBox, recording what was put in front of someone."""

    Yes, No = 1, 0

    def __init__(self) -> None:
        self.questions: list[str] = []
        self.notices: list[str] = []
        self.answer = self.No

    def question(self, _parent, _title, text, *_args) -> int:
        self.questions.append(text)
        return self.answer

    def information(self, _parent, _title, text) -> None:
        self.notices.append(text)

    warning = information


@pytest.fixture
def window(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_autoconnect", lambda _self: None)
    window = MainWindow()

    from macrokey.ui import app as ui_app

    box = FakeBox()
    monkeypatch.setattr(ui_app, "QMessageBox", box)
    window.box = box
    window.flattened: list[bool] = []
    monkeypatch.setattr(
        capture_setup,
        "set_pointer_accel_flat",
        lambda: (window.flattened.append(True), (True, "pointer acceleration is now flat"))[1],
    )
    window.app.settings.pointer_accel_declined = False
    monkeypatch.setattr(window.app.settings, "save", lambda: None)
    return window


def accel(monkeypatch, profile: str | None) -> None:
    monkeypatch.setattr(capture_setup, "pointer_accel_profile", lambda: profile)


def test_nothing_is_said_when_it_is_already_flat(window, monkeypatch) -> None:
    accel(monkeypatch, "flat")

    window._offer_flat_pointer()

    assert window.box.questions == []
    assert window.flattened == []


def test_nothing_is_said_where_the_setting_cannot_be_read(window, monkeypatch) -> None:
    accel(monkeypatch, None)

    window._offer_flat_pointer()

    assert window.box.questions == []


def test_the_change_needs_a_yes(window, monkeypatch) -> None:
    accel(monkeypatch, "default")
    window.box.answer = FakeBox.No

    window._offer_flat_pointer()

    assert len(window.box.questions) == 1
    assert "'default'" in window.box.questions[0]
    assert window.flattened == [], "a no must leave the desktop alone"
    assert window.app.settings.pointer_accel_declined


def test_a_yes_applies_it(window, monkeypatch) -> None:
    accel(monkeypatch, "default")
    window.box.answer = FakeBox.Yes

    window._offer_flat_pointer()

    assert window.flattened == [True]
    assert not window.app.settings.pointer_accel_declined


def test_a_no_is_not_asked_again(window, monkeypatch) -> None:
    accel(monkeypatch, "default")
    window.app.settings.pointer_accel_declined = True

    window._offer_flat_pointer()

    assert window.box.questions == []


def test_the_help_menu_asks_again_after_a_no(window, monkeypatch) -> None:
    """Otherwise "not now" is permanent with no way back to it."""
    accel(monkeypatch, "default")
    window.app.settings.pointer_accel_declined = True

    window._offer_flat_pointer(asked_for=True)

    assert len(window.box.questions) == 1


def test_turning_mouse_capture_on_is_what_raises_it(window, monkeypatch) -> None:
    """Where it becomes relevant. A keyboard-only macro does not care."""
    accel(monkeypatch, "default")

    window._mouse_capture_toggled(False)
    assert window.box.questions == []

    window._mouse_capture_toggled(True)
    assert len(window.box.questions) == 1
