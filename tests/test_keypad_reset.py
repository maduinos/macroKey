"""A keypad that has forgotten its profile, and saying so.

The pad rewrites its own EEPROM with the factory defaults when it cannot read
what is stored there, and it reports that. It reported it into a hole: the LOG
line goes out once, from `setup()`, and the host opens the port, waits for the
board to settle, flushes whatever arrived and only then asks IDENT. So the one
message that explains an empty keypad was discarded every single time, and the
mismatch dialog then offered "Pull from keypad" -- one keystroke from copying
the emptiness over the only remaining copy of the macros.
"""

from __future__ import annotations

import pytest

from macrokey.config import Action, default_profile, is_factory_default
from macrokey.device.protocol import Hello, parse

# ----------------------------------------------------------------- the flag --


def _hello(extra: str = "") -> Hello:
    line = "HELLO proto=1 fw=0.9.1 board=promicro keys=8 leds=1 bytes=1024" + extra
    return Hello.from_message(parse(line))


def test_a_pad_that_kept_its_profile_says_nothing() -> None:
    assert _hello().profile_was_reset is False


def test_a_pad_that_reset_itself_says_so_on_every_hello() -> None:
    """`HELLO` and not a boot LOG, because IDENT re-sends this one."""
    assert _hello(" reset=1").profile_was_reset is True


def test_older_firmware_without_the_key_is_not_treated_as_reset() -> None:
    """Absent means no. An added key must not change what old firmware means."""
    old = Hello.from_message(
        parse("HELLO proto=1 fw=0.9.0 board=promicro keys=8 leds=1 bytes=1024")
    )
    assert old.profile_was_reset is False


# ------------------------------------------------------------ empty or not --


def test_a_fresh_profile_is_factory_default() -> None:
    assert is_factory_default(default_profile())


def test_a_bound_key_is_not_factory_default() -> None:
    profile = default_profile()
    profile.set_action(2, "double", Action(kind="key", hotkey="ctrl+s"))
    assert not is_factory_default(profile)


def test_settings_alone_do_not_count_as_content() -> None:
    """Brightness and colour are settings; losing them is not losing work.

    A pad whose brightness was nudged and nothing else is still empty, and must
    still be recognised as empty when it comes back holding defaults.
    """
    profile = default_profile()
    profile.brightness = 200
    profile.resting_color = "ff0000"
    assert is_factory_default(profile)


# --------------------------------------------------------------- the dialog --

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from macrokey.ui.app import MainWindow  # noqa: E402


class FakeDevice:
    connected = True
    hello = None
    port = "/dev/fake"

    def __init__(self) -> None:
        self.writes = 0

    def write_profile(self, _blob: bytes) -> None:
        self.writes += 1

    def disconnect(self) -> None:
        self.connected = False


@pytest.fixture
def window(monkeypatch, tmp_path):
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_autoconnect", lambda self: None)
    monkeypatch.setattr(MainWindow, "_maybe_fix_capture", lambda self, **_k: None)
    win = MainWindow()
    win._connection_timer.stop()
    win.app.device = FakeDevice()
    win.app.save = lambda: None
    win.app.confirm_on_device = lambda *a, **k: None
    yield win
    win.close()


def _capture_dialog(monkeypatch) -> dict:
    """Answers Cancel, and reports what the box said and which way it leaned."""
    seen: dict = {}

    def fake_exec(self) -> int:
        default = self.defaultButton()
        seen["title"] = self.windowTitle()
        seen["text"] = self.text()
        seen["default"] = default.text().replace("&", "") if default else None
        button = next(b for b in self.buttons() if b.text().replace("&", "") == "Cancel")
        self._clicked = button
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self._clicked)
    return seen


def test_an_ordinary_mismatch_still_leans_on_neither_side(window, monkeypatch) -> None:
    seen = _capture_dialog(monkeypatch)
    window._device_lost_its_profile = False

    window._resolve_profile_mismatch()

    assert seen["title"] == "Profile differs"
    assert seen["default"] == "Cancel"


def test_a_forgetful_keypad_is_named_and_leans_towards_push(window, monkeypatch) -> None:
    """The two cases need opposite answers, so they must not look alike."""
    seen = _capture_dialog(monkeypatch)
    window._device_lost_its_profile = True

    window._resolve_profile_mismatch()

    assert seen["title"] == "The keypad lost its profile"
    assert "factory defaults" in seen["text"]
    assert seen["default"] == "Push to keypad"


def test_cancelling_still_writes_nothing_either_way(window, monkeypatch) -> None:
    _capture_dialog(monkeypatch)
    window._device_lost_its_profile = True

    window._resolve_profile_mismatch()

    assert window.app.device.writes == 0
    assert window._profiles_diverged
