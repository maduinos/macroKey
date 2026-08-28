"""Getting the link back after it drops.

The pad re-enumerates on every firmware upload and whenever the cable is
touched, and it is the only way to start or finish a recording. A drop
therefore leaves hold-to-record dead -- and silently, because holding a key for
three seconds simply stops doing anything. PROTOCOL.md section 5 has always
said the host retries with a backoff; nothing did.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey.ui.app import (  # noqa: E402
    RECONNECT_FIRST_SECONDS,
    RECONNECT_MAX_SECONDS,
    MainWindow,
)


@pytest.fixture
def window(monkeypatch):
    """A window whose link state is ours to move, and that opens no ports."""
    QApplication.instance() or QApplication([])
    # Before the window exists, because the startup connect is queued with the
    # bound method during __init__ -- and pytest-qt pumps the event loop between
    # setup and the test body, so it really does run and really would count as
    # a reconnect attempt.
    monkeypatch.setattr(MainWindow, "_autoconnect", lambda _self: None)
    window = MainWindow()

    state = {"connected": False}
    monkeypatch.setattr(
        type(window.app.device),
        "connected",
        property(lambda _self: state["connected"]),
    )
    window.attempts = []
    window.plug = lambda: state.__setitem__("connected", True)
    window.unplug = lambda: state.__setitem__("connected", False)
    monkeypatch.setattr(
        window, "_toggle_connection", lambda **kw: window.attempts.append(kw)
    )
    return window


def establish_then_drop(window) -> None:
    """A link that worked, and then did not."""
    window.plug()
    window._poll_connection()
    window.unplug()
    window._poll_connection()


def test_a_link_that_dropped_is_retried(window) -> None:
    establish_then_drop(window)
    assert window._reconnect_at is not None
    assert window.attempts == [], "the first backoff has not elapsed yet"

    window._reconnect_at = 0.0  # as if it had
    window._poll_connection()

    assert window.attempts == [{"quiet": True}], "no dialog: the pad may simply be gone"


def test_a_link_that_was_never_up_is_left_alone(window) -> None:
    """Startup with no pad plugged in must not become a permanent port scan.

    Opening a serial port is not free -- plenty of boards reset when theirs is
    opened -- so probing every candidate on a timer would be poking unrelated
    hardware for as long as the editor stays open.
    """
    window._poll_connection()
    window._poll_connection()

    assert window._reconnect_at is None
    assert window.attempts == []


def test_an_explicit_disconnect_is_not_undone(window) -> None:
    window.plug()
    window._poll_connection()

    window._disconnect()
    window.unplug()
    window._poll_connection()

    assert window._reconnect_at is None
    assert window.attempts == []


def test_the_backoff_grows_and_then_stops_growing(window) -> None:
    establish_then_drop(window)

    seen = []
    for _ in range(8):
        seen.append(window._reconnect_delay)
        window._reconnect_at = 0.0
        window._poll_connection()

    assert seen[:4] == [1.0, 2.0, 4.0, 8.0]
    assert seen[0] == RECONNECT_FIRST_SECONDS
    assert window._reconnect_delay == RECONNECT_MAX_SECONDS
    assert len(window.attempts) == 8


def test_reconnecting_starts_over_once_the_pad_is_back(window) -> None:
    establish_then_drop(window)
    window._reconnect_at = 0.0
    window._poll_connection()
    assert window._reconnect_delay > RECONNECT_FIRST_SECONDS

    window.plug()
    window._poll_connection()

    assert window._reconnect_at is None
    assert window._reconnect_delay == RECONNECT_FIRST_SECONDS


def test_a_recording_in_progress_is_not_interrupted_to_reconnect(window) -> None:
    """Connecting reads the pad's profile and may raise the mismatch dialog.
    Doing that under a running recording would put a modal in front of someone
    who is being captured, and the session has its own watchdog for a pad that
    really is gone.
    """
    establish_then_drop(window)
    window.session.active_key = 3
    window._reconnect_at = 0.0

    window._poll_connection()

    assert window.attempts == []
    assert window._reconnect_at == 0.0, "still armed for when the recording ends"
