"""The toolbar's Reset, which clears the pad as well as this computer.

The pad is cleared through its own RESET verb rather than by pushing the host
defaults: ``Profile::writeDefaults`` zeroes the macro region outright, and it is
the definition both sides are built to agree on.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from macrokey.config.model import Action, Profile, default_profile  # noqa: E402
from macrokey.device import DeviceError  # noqa: E402
from macrokey.ui.app import MainWindow  # noqa: E402


class FakeDevice:
    def __init__(self, connected: bool = True, fails: bool = False) -> None:
        self.connected = connected
        self._fails = fails
        self.resets = 0

    def reset_defaults(self) -> None:
        if self._fails:
            raise DeviceError("pad said ERR")
        self.resets += 1

    def disconnect(self) -> None:
        """Closing the window calls this through MacroKeyApp.close()."""
        self.connected = False


def edited_profile() -> Profile:
    profile = default_profile()
    profile.set_action(0, "tap", Action(kind="sequence", slot=0))
    profile.device_macros = [[Action(kind="delay", delay_ms=200)]]
    profile.brightness = 200
    return profile


@pytest.fixture
def window(monkeypatch):
    QApplication.instance() or QApplication([])
    # Both are queued with singleShot in __init__ and would otherwise fire into
    # a fake device from whatever event loop spins next -- on a worker thread,
    # in some later test.
    monkeypatch.setattr(MainWindow, "_autoconnect", lambda self: None)
    monkeypatch.setattr(MainWindow, "_maybe_fix_capture", lambda self: None)
    win = MainWindow()
    win._connection_timer.stop()
    win.app.save = lambda: None
    win.app.confirm_on_device = lambda *a, **k: None
    return win


def answer(monkeypatch, label: str) -> None:
    """Clicks the named button in whatever QMessageBox `exec` puts up."""

    def fake_exec(self) -> int:
        for button in self.buttons():
            if button.text().replace("&", "") == label:
                self.setResult(0)
                self._clicked = button  # keeps clickedButton() honest
                return 0
        raise AssertionError(f"no {label!r} button in {[b.text() for b in self.buttons()]}")

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self._clicked)


def test_reset_clears_the_pad_and_this_computer(window, monkeypatch) -> None:
    window.app.device = FakeDevice()
    window.app.profile = edited_profile()
    answer(monkeypatch, "Reset")

    window._reset_everything()

    assert window.app.device.resets == 1
    assert window.app.profile.to_dict() == default_profile().to_dict()
    assert window.app.profile.device_macros == []


def test_cancel_touches_neither_side(window, monkeypatch) -> None:
    window.app.device = FakeDevice()
    before = edited_profile()
    window.app.profile = before
    answer(monkeypatch, "Cancel")

    window._reset_everything()

    assert window.app.device.resets == 0
    assert window.app.profile is before


def test_a_pad_that_refuses_leaves_the_host_profile_alone(window, monkeypatch) -> None:
    """Otherwise the computer is empty and the pad still fires eight bindings
    that nothing on screen admits to."""
    window.app.device = FakeDevice(fails=True)
    before = edited_profile()
    window.app.profile = before
    answer(monkeypatch, "Reset")
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)

    window._reset_everything()

    assert window.app.profile is before


def test_reset_offline_still_clears_this_computer(window, monkeypatch) -> None:
    window.app.device = FakeDevice(connected=False)
    window.app.profile = edited_profile()
    answer(monkeypatch, "Reset")

    window._reset_everything()

    assert window.app.device.resets == 0
    assert window.app.profile.to_dict() == default_profile().to_dict()


def test_the_toolbar_follows_the_reset(window, monkeypatch) -> None:
    """_refresh_all runs, so brightness and typing speed are not left showing
    the profile that was just thrown away."""
    window.app.device = FakeDevice()
    window.app.profile = edited_profile()
    window._refresh_all()
    assert window.brightness.value() == 200
    answer(monkeypatch, "Reset")

    window._reset_everything()

    assert window.brightness.value() == default_profile().brightness
    assert window.text_speed.value() == 5
