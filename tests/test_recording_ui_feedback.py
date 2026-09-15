"""Recording feedback must describe captured input and actual save outcomes."""


import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey import i18n  # noqa: E402
from macrokey.recorder.events import RawEvent  # noqa: E402
from macrokey.session import RecordOutcome  # noqa: E402
from macrokey.ui.app import MainWindow  # noqa: E402


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])  # noqa: F841
    i18n.set_language("en")
    monkeypatch.setattr("macrokey.ui.app.QTimer.singleShot", lambda *args: None)
    monkeypatch.setattr(MainWindow, "_rescan_ports", lambda self: None)
    win = MainWindow()
    win._connection_timer.stop()
    yield win
    win.session.active_key = None
    win.close()
    win.deleteLater()



def test_live_hold_stays_visible_without_new_events(window, monkeypatch):
    window.session.active_key = 0
    window.session._started_at = 100
    monkeypatch.setattr("macrokey.ui.app.time.monotonic", lambda: 101)
    window._refresh_recording()
    window._append_live_capture(RawEvent(kind="key_down", token="w", at=101))
    monkeypatch.setattr("macrokey.ui.app.time.monotonic", lambda: 165)
    window._update_recording_stats()
    assert "01:05" in window.record_stats.text()
    assert "held keys: w" in window.record_stats.text()
    window._refresh_recording()
    assert "held keys: w" in window.record_stats.text()
    window._append_live_capture(RawEvent(kind="key_up", token="w", at=166))
    assert "none held" in window.record_stats.text()


def test_late_events_cannot_pollute_a_new_recording(window):
    window.session.active_key = 0
    window.session._started_at = 200
    window._append_live_capture(RawEvent(kind="key_down", token="w", at=100))
    assert window._live_count == 0
    assert not window._live_keys


def test_save_failure_takes_priority_over_device_destination(window):
    outcome = RecordOutcome(0, 1, "on the keypad: slot 1", True, error="write failed")
    window.session.last_steps = [{"type": "hotkey", "params": {"hotkey": "w"}}]
    window._show_capture(outcome)
    assert "write failed" in window.capture_title.text()
    assert "not saved" in window.save_state.text()
    assert window.capture_details.isChecked()
    assert window.capture_list.item(0).text() == "write failed"


def test_local_save_is_not_reported_as_a_device_save(window, monkeypatch):
    monkeypatch.setattr(window.app, "save", lambda: None)
    window._apply("test edit")
    assert window.save_state.text() == "Saved on this PC · keypad not connected"


def test_pc_save_failure_does_not_claim_local_success(window, monkeypatch):
    def fail():
        raise OSError("read-only")
    monkeypatch.setattr(window.app, "save", fail)
    window._apply("test edit")
    assert window.save_state.text() == "Could not save changes to this PC"


def test_live_log_has_a_bounded_number_of_rows(window):
    window.session.active_key = 0
    window.session._started_at = 1
    for index in range(550):
        window._append_live_capture(
            RawEvent(kind="mouse_move", token="move", at=index + 1, data=(1, 0))
        )
    assert window.capture_list.count() == 500
    assert window._live_count == 550
