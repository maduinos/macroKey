"""Hold a key to record into it, hold it again to keep it.

The device reports "key N was held alone" and nothing else; every decision about
what that means lives in the session. These cover the decisions, including the
ones that only happen when something has gone wrong -- which on a pad with one
pixel and no screen are the ones nobody can debug by looking.
"""

from __future__ import annotations

import pytest

from macrokey.device import DeviceError
from macrokey.session import RecordingSession


class FakeDevice:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.connected = True
        self.colors: list[tuple[int, int, int]] = []
        self.modes: list[bool] = []

    def set_led_mode(self, host: bool, timeout_ms: int | None = None) -> None:
        if self.fail:
            raise DeviceError("no link")
        self.modes.append(host)

    def set_all(self, color, effect: str = "solid", period: int = 0) -> None:
        if self.fail:
            raise DeviceError("no link")
        self.colors.append(tuple(color))


class FakeApp:
    """Only what the session touches."""

    def __init__(self, steps=None, **behaviour) -> None:
        self.device = FakeDevice(fail=behaviour.get("device_fails", False))
        self.steps = steps if steps is not None else [{"type": "hotkey", "params": {"hotkey": "a"}}]
        self.messages: list[str] = []
        self.last_redacted = behaviour.get("redacted", 0)
        self.started = False
        self.saved = False
        self.pushed = False
        self._start_raises = behaviour.get("start_raises", False)
        self._assign_raises = behaviour.get("assign_raises", False)
        self._push_raises = behaviour.get("push_raises", False)
        self._where = behaviour.get("where", "on the keypad: a")
        self.requested: list[int] = []

    def status(self, message: str) -> None:
        self.messages.append(message)

    def request_record(self, key: int, gesture: str = "tap") -> None:
        """The real one hands this to the record worker."""
        self.requested.append((key, gesture))

    def start_recording(self, on_event=None) -> None:
        if self._start_raises:
            raise RuntimeError("no input devices")
        self.started = True

    def stop_recording(self):
        self.started = False
        return self.steps

    def assign_recording(self, steps, layer, key, gesture, name=""):
        if self._assign_raises:
            raise ValueError("device macro storage exhausted")
        self.assigned = (tuple(steps), layer, key, gesture)
        return self._where

    def save(self) -> None:
        self.saved = True

    def push_profile(self) -> None:
        if self._push_raises:
            raise DeviceError("link lost")
        self.pushed = True


def session_for(**behaviour):
    app = FakeApp(**behaviour)
    changes = []
    return app, RecordingSession(app, on_change=lambda: changes.append(1)), changes


# ------------------------------------------------------------- the happy path --


def test_the_first_hold_starts_recording() -> None:
    app, session, _ = session_for()
    session.handle_request(2)
    assert session.recording is True
    assert session.active_key == 2
    assert app.started is True


def test_holding_the_same_key_again_stores_it_on_that_key() -> None:
    app, session, _ = session_for()
    session.handle_request(2)
    session.handle_request(2)
    assert session.recording is False
    assert app.assigned[2] == 2, "stored against the key that was held"
    assert app.assigned[3] == "tap"
    assert app.saved and app.pushed


def test_the_result_says_whether_the_pad_can_replay_it_alone() -> None:
    _, session, _ = session_for(where="on the keypad: a")
    session.handle_request(0)
    session.handle_request(0)
    assert session.last_outcome.on_device is True

    _, session, _ = session_for(where="host action #3 (2 steps)")
    session.handle_request(0)
    session.handle_request(0)
    assert session.last_outcome.on_device is False


def test_each_transition_notifies_once() -> None:
    _, session, changes = session_for()
    session.handle_request(1)
    session.handle_request(1)
    assert len(changes) == 2


# ------------------------------------------------------------------- mistakes --


def test_holding_a_different_key_mid_recording_does_not_switch_targets() -> None:
    """Storing into the key that was held second would bind the wrong slot."""
    app, session, _ = session_for()
    session.handle_request(2)
    session.handle_request(5)
    assert session.active_key == 2, "still recording into the original key"
    assert not hasattr(app, "assigned")
    assert any("Already recording" in message for message in app.messages)


def test_capturing_nothing_leaves_the_key_alone() -> None:
    app, session, _ = session_for(steps=[])
    session.handle_request(3)
    session.handle_request(3)
    assert not hasattr(app, "assigned")
    assert session.last_outcome.error == "nothing was captured"
    assert not app.saved


def test_a_capture_backend_that_will_not_start_reports_and_stays_idle() -> None:
    app, session, _ = session_for(start_raises=True)
    session.handle_request(0)
    assert session.recording is False
    assert any("Cannot record" in message for message in app.messages)


def test_a_full_profile_is_reported_rather_than_raised() -> None:
    app, session, _ = session_for(assign_raises=True)
    session.handle_request(0)
    session.handle_request(0)
    assert session.recording is False
    assert "exhausted" in session.last_outcome.error
    assert not app.saved


def test_a_failed_write_says_so_and_does_not_claim_success() -> None:
    app, session, _ = session_for(push_raises=True)
    session.handle_request(0)
    session.handle_request(0)
    assert "link lost" in session.last_outcome.error
    assert app.saved is True, "the profile is still kept on disk"


def test_a_dead_link_does_not_stop_the_recording_working() -> None:
    """The pixel is feedback. Losing it must not lose the macro."""
    app, session, _ = session_for(device_fails=True)
    session.handle_request(1)
    assert session.recording is True
    session.handle_request(1)
    assert app.pushed is True


def test_dropped_secrets_are_carried_into_the_outcome() -> None:
    _, session, _ = session_for(redacted=1)
    session.handle_request(0)
    session.handle_request(0)
    assert session.last_outcome.dropped_secrets == 1


# --------------------------------------------------------------------- aborts --


def test_aborting_discards_without_storing() -> None:
    app, session, _ = session_for()
    session.handle_request(4)
    session.abort()
    assert session.recording is False
    assert not hasattr(app, "assigned")


def test_aborting_when_idle_does_nothing() -> None:
    app, session, changes = session_for()
    session.abort()
    assert changes == []
    assert app.messages == []


# ------------------------------------------------------------------ watchdog --


def _run_watchdog(monkeypatch, session, seconds: float = 2.0):
    """Drives the watchdog fast enough to observe, then waits for it to act."""
    import time as _time

    import macrokey.session as session_module

    monkeypatch.setattr(session_module, "WATCH_SECONDS", 0.01)
    session._start_watchdog()
    deadline = _time.monotonic() + seconds
    while _time.monotonic() < deadline:
        _time.sleep(0.01)
        yield


def test_the_watchdog_keeps_the_recording_colour_alive(monkeypatch) -> None:
    """The pad drops back to its own scene after LED_HOLD_MS of silence, so a
    long recording has to keep saying so."""
    app, session, _ = session_for()
    session.handle_request(0)
    before = len(app.device.colors)
    for _ in _run_watchdog(monkeypatch, session):
        if len(app.device.colors) > before:
            break
    assert len(app.device.colors) > before


def test_a_disconnected_pad_drops_the_recording(monkeypatch) -> None:
    """The pad is the only way to finish one. Unplugged, nothing would ever
    arrive to stop it and global capture would stay on for the session."""
    app, session, _ = session_for()
    session.handle_request(0)
    assert session.recording
    app.device.connected = False
    for _ in _run_watchdog(monkeypatch, session):
        if not session.recording:
            break
    assert not session.recording
    assert app.started is False
    assert any("disconnected" in message for message in app.messages)


def test_a_recording_that_runs_too_long_is_stored_rather_than_left_open(
    monkeypatch,
) -> None:
    """Finishing goes through the record worker, not the watchdog thread: the
    worker owns start and finish so the two cannot interleave."""
    import macrokey.session as session_module

    monkeypatch.setattr(session_module, "MAX_RECORDING_SECONDS", 0.0)
    app, session, _ = session_for()
    session.handle_request(0)
    for _ in _run_watchdog(monkeypatch, session):
        if app.requested:
            break
    assert app.requested == [(0, "tap")]


@pytest.mark.parametrize("key", range(8))
def test_every_key_can_be_recorded_into(key: int) -> None:
    app, session, _ = session_for()
    session.handle_request(key)
    session.handle_request(key)
    assert app.assigned[2] == key


# ------------------------------------------------ which thread handles it --


def test_a_record_request_is_not_handled_on_the_serial_reader_thread() -> None:
    """Handling it inline means the reader thread waits for a reply only the
    reader thread can deliver. It never deadlocked outright -- every device call
    just timed out -- so recording started with no red pixel and finishing
    ground through twenty timeouts before failing to save.
    """
    import threading
    import time

    from macrokey.app import MacroKeyApp
    from macrokey.device.protocol import RecordRequest

    app = MacroKeyApp.__new__(MacroKeyApp)  # no settings, no serial port
    app._record_queue = __import__("queue").Queue()
    app._record_thread = None
    app._status_callbacks = []
    app._event_callbacks = []

    seen: list[tuple[int, str]] = []

    class Session:
        def handle_request(self, key: int, gesture: str = "tap") -> None:
            seen.append((key, threading.current_thread().name))

    app.session = Session()
    caller = threading.current_thread().name

    MacroKeyApp._on_device_event(app, RecordRequest(key=3))

    deadline = time.monotonic() + 2.0
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)

    assert seen, "the request was never handled"
    key, thread_name = seen[0]
    assert key == 3
    assert thread_name != caller, "handled inline on the delivering thread"


def test_requests_are_handled_in_order_by_a_single_worker() -> None:
    """Start and finish are two halves of one state machine. A thread per
    request would let a finish overtake the start it belongs to."""
    import queue as _queue
    import threading
    import time

    from macrokey.app import MacroKeyApp
    from macrokey.device.protocol import RecordRequest

    app = MacroKeyApp.__new__(MacroKeyApp)
    app._record_queue = _queue.Queue()
    app._record_thread = None
    app._status_callbacks = []
    app._event_callbacks = []

    seen: list[int] = []
    threads: set[str] = set()

    class Session:
        def handle_request(self, key: int, gesture: str = "tap") -> None:
            threads.add(threading.current_thread().name)
            time.sleep(0.02)  # a real one writes the whole profile
            seen.append(key)

    app.session = Session()
    for key in (0, 0, 5, 5):
        MacroKeyApp._on_device_event(app, RecordRequest(key=key))

    deadline = time.monotonic() + 3.0
    while len(seen) < 4 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert seen == [0, 0, 5, 5]
    assert len(threads) == 1
