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

    def status(self, message: str) -> None:
        self.messages.append(message)

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


def test_refreshing_the_led_while_idle_is_a_no_op() -> None:
    app, session, _ = session_for()
    session.refresh_led()
    assert app.device.colors == []


def test_refreshing_the_led_while_recording_re_sends_the_colour() -> None:
    app, session, _ = session_for()
    session.handle_request(0)
    before = len(app.device.colors)
    session.refresh_led()
    assert len(app.device.colors) == before + 1


@pytest.mark.parametrize("key", range(8))
def test_every_key_can_be_recorded_into(key: int) -> None:
    app, session, _ = session_for()
    session.handle_request(key)
    session.handle_request(key)
    assert app.assigned[2] == key
