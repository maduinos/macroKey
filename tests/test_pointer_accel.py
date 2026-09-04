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

from macrokey.config import model  # noqa: E402
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


# ----------------------------------------------------------------- windows --
#
# Windows keeps the same setting behind "Enhance pointer precision", which is
# the third member of the SPI_GETMOUSE triple. None of this can run on the
# machine it was written on, so the Win32 call is faked at the one seam that
# touches it -- enough to prove the branch reads the right member, preserves
# the two thresholds, and reports a write that did not take.


class FakeMouseParams:
    """Stands in for SystemParametersInfoW over the SPI_GETMOUSE triple."""

    GET, SET = 0x0003, 0x0004

    def __init__(self, values, *, writable=True):
        self.values = list(values)
        self.writable = writable
        self.written = None
        self.flags = None

    def __call__(self, action, _ui, pv, flags):
        array = pv._obj
        if action == self.GET:
            for index, value in enumerate(self.values):
                array[index] = value
            return 1
        if action == self.SET:
            if not self.writable:
                return 0
            self.written = [array[0], array[1], array[2]]
            self.flags = flags
            self.values = list(self.written)
            return 1
        raise AssertionError(action)


@pytest.fixture
def windows(monkeypatch):
    import ctypes
    import types

    monkeypatch.setattr(capture_setup.sys, "platform", "win32")

    def install(params: FakeMouseParams) -> FakeMouseParams:
        user32 = types.SimpleNamespace(SystemParametersInfoW=params)
        monkeypatch.setattr(
            ctypes, "windll", types.SimpleNamespace(user32=user32), raising=False
        )
        return params

    return install


def test_enhance_pointer_precision_on_reads_as_not_flat(windows) -> None:
    windows(FakeMouseParams([6, 10, 1]))

    assert capture_setup.pointer_accel_profile() == "enhanced"
    assert not capture_setup.pointer_accel_is_flat()
    assert capture_setup.pointer_accel_can_be_flattened()


def test_enhance_pointer_precision_off_reads_as_flat(windows) -> None:
    windows(FakeMouseParams([6, 10, 0]))

    assert capture_setup.pointer_accel_profile() == "flat"
    assert not capture_setup.pointer_accel_can_be_flattened()


def test_flattening_keeps_the_speed_thresholds(windows) -> None:
    """Only the acceleration member is cleared.

    Writing [0, 0, 0] would also discard a tuned pointer speed, which is a
    different setting the person did not ask us to touch.
    """
    params = windows(FakeMouseParams([6, 10, 1]))

    ok, _message = capture_setup.set_pointer_accel_flat()

    assert ok
    assert params.written == [6, 10, 0]
    # Persisted across a reboot, and visible to programs already running.
    assert params.flags == (
        capture_setup._SPIF_UPDATEINIFILE | capture_setup._SPIF_SENDCHANGE
    )


def test_a_refused_windows_write_is_reported_as_failure(windows) -> None:
    windows(FakeMouseParams([6, 10, 1], writable=False))

    ok, message = capture_setup.set_pointer_accel_flat()

    assert not ok
    assert "could not change" in message


def test_the_undo_hint_names_the_windows_checkbox(windows) -> None:
    windows(FakeMouseParams([6, 10, 1]))
    assert "Enhance pointer precision" in capture_setup.pointer_accel_undo_hint()


def test_the_undo_hint_is_the_gsettings_line_off_windows() -> None:
    assert "gsettings set" in capture_setup.pointer_accel_undo_hint()


# ------------------------------------------------------------- the offer --


class FakeDialog:
    """One non-modal question, answered the moment it is opened.

    The real dialog stays on screen and calls back when someone clicks. Here
    `open()` invokes the callback straight away with the answer the test set,
    which keeps these tests synchronous without pretending the window is.
    """

    def __init__(self, box: FakeBox) -> None:
        self._box = box
        self._callback = None

    def setIcon(self, _icon) -> None:
        pass

    def setWindowTitle(self, title) -> None:
        self._box.titles.append(title)

    def setText(self, text) -> None:
        self._box.questions.append(text)

    def setStandardButtons(self, _buttons) -> None:
        pass

    def setDefaultButton(self, _button) -> None:
        pass

    def setAttribute(self, _attribute) -> None:
        pass

    @property
    def finished(self) -> FakeDialog:
        return self

    def connect(self, callback) -> None:
        self._callback = callback

    def open(self) -> None:
        if self._callback is not None:
            self._callback(self._box.answer)

    def close(self) -> None:
        pass


class FakeBox:
    """Stands in for QMessageBox, recording what was put in front of someone."""

    Yes, No, Ok, Cancel = 1, 0, 2, 4
    Question = Warning = Information = object()

    def __init__(self) -> None:
        self.questions: list[str] = []
        self.titles: list[str] = []
        self.notices: list[str] = []
        self.answer = self.No

    def __call__(self, _parent) -> FakeDialog:
        """`QMessageBox(self)` -- the questions are built, not called, now."""
        return FakeDialog(self)

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


# ------------------------------------------- the one evidence-backed re-ask --
#
# The startup question is asked before there is anything to point at, and a no
# there is remembered for good. These cover the single follow-up allowed once a
# recording exists that the setting will actually move.


def drag_macro(*slices: int) -> list:
    """A macro of `slices` movement slices, each its own pause-separated run."""
    steps = []
    for counts in slices:
        while counts > 127:  # a slice past a signed byte becomes several records
            steps.append(model.Action(kind="mouse_move", dx=127))
            counts -= 127
        steps.append(model.Action(kind="mouse_move", dx=counts))
        steps.append(model.Action(kind="delay", delay_ms=50))
    return steps


def test_a_slow_drag_is_not_worth_warning_about() -> None:
    """It is replayed a count at a time, at the speed the hand made it, so
    whatever curve is in force applies to the replay as it applied to the
    hand."""
    assert model.accel_sensitive_macro_slots([drag_macro(20, 25)]) == []


def test_a_fast_drag_is() -> None:
    assert model.accel_sensitive_macro_slots([drag_macro(100)]) == [0]


def test_a_slice_split_across_records_is_measured_whole() -> None:
    """227 counts is one slice the host had to write down twice, not two slow
    ones -- `runMoves` replays it as one, so it is one here too."""
    assert model.accel_sensitive_macro_slots([drag_macro(227)]) == [0]


def test_a_fast_recording_reopens_the_question_once(window, monkeypatch) -> None:
    accel(monkeypatch, "default")
    window.app.settings.pointer_accel_declined = True
    window.app.profile.device_macros = [drag_macro(100)]

    window._offer_flat_pointer_for_fast_macro()

    assert len(window.box.questions) == 1
    assert "lands short" in window.box.questions[0], "it has to say what changed"
    assert window.app.settings.pointer_accel_evidence_shown

    # And never again: a no that keeps being re-asked is not being respected.
    window._offer_flat_pointer_for_fast_macro()
    assert len(window.box.questions) == 1


def test_a_slow_recording_says_nothing(window, monkeypatch) -> None:
    accel(monkeypatch, "default")
    window.app.settings.pointer_accel_declined = True
    window.app.profile.device_macros = [drag_macro(20)]

    window._offer_flat_pointer_for_fast_macro()

    assert window.box.questions == []
    assert not window.app.settings.pointer_accel_evidence_shown, "unspent"


def test_the_follow_up_is_not_spent_on_someone_who_never_declined(
    window, monkeypatch
) -> None:
    """The startup offer still speaks for itself; this would be a second copy
    of the same question in one session."""
    accel(monkeypatch, "default")
    window.app.settings.pointer_accel_declined = False
    window.app.profile.device_macros = [drag_macro(100)]

    window._offer_flat_pointer_for_fast_macro()

    assert window.box.questions == []


def test_nothing_is_said_when_the_desktop_is_already_flat(window, monkeypatch) -> None:
    accel(monkeypatch, "flat")
    window.app.settings.pointer_accel_declined = True
    window.app.profile.device_macros = [drag_macro(100)]

    window._offer_flat_pointer_for_fast_macro()

    assert window.box.questions == []
