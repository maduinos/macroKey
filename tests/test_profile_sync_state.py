"""A cancelled profile mismatch must remain a real, enforced state."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from macrokey.app import MacroKeyApp  # noqa: E402
from macrokey.config import Action, default_profile  # noqa: E402
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
def window(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_autoconnect", lambda self: None)
    monkeypatch.setattr(MainWindow, "_maybe_fix_capture", lambda self: None)
    win = MainWindow()
    win._connection_timer.stop()
    win.app.device = FakeDevice()
    win.app.save = lambda: None
    win.app.confirm_on_device = lambda *a, **k: None
    yield win
    win.close()


def _answer_cancel(monkeypatch) -> None:
    def fake_exec(self) -> int:
        button = next(b for b in self.buttons() if b.text().replace("&", "") == "Cancel")
        self._clicked = button
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self._clicked)


def test_cancelled_mismatch_blocks_automatic_device_overwrite(window, monkeypatch) -> None:
    _answer_cancel(monkeypatch)
    window._resolve_profile_mismatch()

    window.app.profile.brightness = 123
    window._apply("Brightness 123")

    assert window._profiles_diverged is True
    assert window.app.device.writes == 0
    assert "saved locally" in window.statusBar().currentMessage()


def test_edit_during_connection_handshake_cannot_queue_a_device_overwrite(window) -> None:
    """A tty is briefly open before IDENT/profile reconciliation completes."""
    window._connecting = True
    window.app.profile.brightness = 123

    window._apply("Brightness 123")

    assert window.app.device.writes == 0
    assert "saved locally" in window.statusBar().currentMessage()


def test_profile_controls_lock_during_device_reconciliation(window) -> None:
    window._connecting = True
    window._refresh_connection()

    assert not window.brightness.isEnabled()
    assert not window.text_speed.isEnabled()
    assert not window.reset_button.isEnabled()
    assert not window.swatch.isEnabled()
    assert not next(iter(window.buttons.values())).isEnabled()
    assert not window._profile_menu.isEnabled()

    window._connecting = False
    window._refresh_connection()
    assert window.brightness.isEnabled()
    assert next(iter(window.buttons.values())).isEnabled()


def test_pad_recording_reveals_live_log_and_defers_profile_writes(window) -> None:
    modal = QDialog(window)
    modal.setModal(True)
    modal.show()
    QApplication.processEvents()
    window.session.active_key = 0

    window._refresh_recording()
    window.app.profile.brightness = 123
    window._apply("Brightness 123")
    window._end_preview()  # must not release the recording session's red LED
    window.session.active_key = None

    assert not modal.isVisible()
    assert not window.record_banner.isHidden()
    assert window.app.device.writes == 0


def test_sync_state_is_visible_and_can_be_resolved(window) -> None:
    window._profiles_diverged = True
    window._refresh_connection()
    assert "needed" in window.sync_button.text().lower()
    assert "differ" in window.link_label.text().lower()

    window._finish_sync("done")
    assert window._profiles_diverged is False
    assert window.sync_button.text() == "Sync…"


def test_directly_replacing_a_recording_reclaims_its_macro_slot(
    window, monkeypatch
) -> None:
    profile = default_profile()
    profile.device_macros = [[Action(kind="delay", delay_ms=100)]]
    profile.set_action(0, "tap", Action(kind="sequence", slot=0))
    window.app.profile = profile
    window._apply = lambda _what: None

    class Dialog:
        result_action = Action()
        repeat_applied = False

        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self) -> int:
            return QDialog.Accepted

    monkeypatch.setattr("macrokey.ui.app.SlotDialog", Dialog)
    window._edit(0, "tap")

    assert window.app.profile.action(0, "tap").is_empty
    assert window.app.profile.device_macros[0] == []


def test_explicit_auto_does_not_reuse_a_remembered_port() -> None:
    app = MacroKeyApp.__new__(MacroKeyApp)
    app.settings = type("Settings", (), {"port": "/dev/old"})()
    opened: list[str] = []
    app.device = type("Device", (), {"connect": lambda _self, port: opened.append(port)})()

    app.connect("")

    assert opened == [""]


def test_an_explicit_manual_port_is_tried_even_when_discovery_cannot_list_it() -> None:
    app = MacroKeyApp.__new__(MacroKeyApp)
    app.settings = type("Settings", (), {"port": "/dev/old"})()
    app._closed = threading.Event()
    opened: list[str] = []
    app.device = type("Device", (), {"connect": lambda _self, port: opened.append(port)})()

    app.connect("/dev/manual-port")

    assert opened == ["/dev/manual-port"]
