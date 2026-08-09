"""Tap-tap-hold records into the double slot; a plain hold records into tap.

Before this the pad could only ever record into tap, so a key's second slot was
reachable only by opening the editor window -- on a device whose whole premise
is that you program it by holding its keys.

Worse, the firmware already let a double-tap-and-hold reach the record path:
`recordFired` is only reset on a press edge that is *not* the second of a pair,
so tap-tap-hold opened the recorder and the host stored the result on the tap
slot without saying so.
"""

from __future__ import annotations

import pytest
from test_recording_session import session_for  # tests/ is on the path, not a package

from macrokey.device import protocol
from macrokey.session import RECORDING_COLOR, RECORDING_DOUBLE_COLOR


def parse(line: str):
    return protocol.parse_event(protocol.parse(line))


# ------------------------------------------------------------------ the wire --


def test_the_gesture_comes_off_the_wire() -> None:
    assert parse("EV t=record k=2 g=double ms=100").gesture == "double"
    assert parse("EV t=record k=2 g=tap ms=100").gesture == "tap"


def test_firmware_without_the_field_still_means_tap() -> None:
    """A pad on older firmware sends no `g`, and tap is what it always did."""
    assert parse("EV t=record k=2 ms=100").gesture == "tap"


@pytest.mark.parametrize("gesture", ["hold", "", "nonsense", "HOLD"])
def test_a_gesture_that_cannot_be_bound_falls_back_to_tap(gesture: str) -> None:
    """`hold` is how recording starts, so it is never a destination. Anything
    unrecognised must not reach set_action, which would refuse it and lose the
    recording that had just been made."""
    assert parse(f"EV t=record k=2 g={gesture} ms=100").gesture == "tap"


# --------------------------------------------------------------- the session --


def test_a_plain_hold_records_into_tap() -> None:
    app, session, _ = session_for()
    session.handle_request(3, "tap")
    session.handle_request(3, "tap")
    assert (app.assigned[1], app.assigned[2]) == (3, "tap")


def test_tap_tap_hold_records_into_double() -> None:
    app, session, _ = session_for()
    session.handle_request(3, "double")
    session.handle_request(3, "double")
    assert (app.assigned[1], app.assigned[2]) == (3, "double")


def test_the_slot_is_decided_when_recording_starts() -> None:
    """Finishing only has to say "this key again". If the finish were what
    chose the slot, ending a tap recording with a tap-tap-hold would silently
    move it to the other slot -- and the pixel would have been lying for the
    whole recording."""
    app, session, _ = session_for()
    session.handle_request(3, "tap")
    session.handle_request(3, "double")  # sloppy finish, same key
    assert (app.assigned[1], app.assigned[2]) == (3, "tap")


def test_the_two_slots_of_one_key_are_independent() -> None:
    app, session, _ = session_for()
    session.handle_request(5, "tap")
    session.handle_request(5, "tap")
    assert (app.assigned[1], app.assigned[2]) == (5, "tap")
    session.handle_request(5, "double")
    session.handle_request(5, "double")
    assert (app.assigned[1], app.assigned[2]) == (5, "double")


def test_a_different_key_is_still_refused_mid_recording() -> None:
    app, session, _ = session_for()
    session.handle_request(1, "tap")
    session.handle_request(4, "double")
    assert session.recording
    assert session.active_key == 1
    assert any("Already recording" in message for message in app.messages)


# -------------------------------------------------------------------- the LED --


def test_the_pixel_says_which_slot_is_being_programmed() -> None:
    """One pixel, no screen: while a recording is running there is nowhere else
    to say which of the key's two slots it is going to land in."""
    app, session, _ = session_for()
    session.handle_request(0, "tap")
    assert app.device.colors[-1] == RECORDING_COLOR

    app, session, _ = session_for()
    session.handle_request(0, "double")
    assert app.device.colors[-1] == RECORDING_DOUBLE_COLOR
    assert RECORDING_DOUBLE_COLOR != RECORDING_COLOR


def test_the_status_line_names_the_slot() -> None:
    app, session, _ = session_for()
    session.handle_request(2, "double")
    assert any("double" in message for message in app.messages)
