"""Translating kernel input events, where the alias tables are the trap.

evdev returns a name, a list of names, or a tuple of names depending on the
code. BTN_LEFT is ("BTN_LEFT", "BTN_MOUSE"), and code that only unwrapped lists
dropped every mouse button on the floor: scrolling recorded, clicking did not,
and nothing reported a problem.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("evdev")

from evdev import ecodes  # noqa: E402

from macrokey.recorder.evdev_source import EvdevRecorder, _token_for  # noqa: E402


@pytest.fixture
def captured():
    events: list = []
    return events, EvdevRecorder(events.append, capture_mouse=True)


def key_event(code: int, value: int):
    return types.SimpleNamespace(type=ecodes.EV_KEY, code=code, value=value)


def rel_event(code: int, value: int):
    return types.SimpleNamespace(type=ecodes.EV_REL, code=code, value=value)


# -------------------------------------------------------------------- buttons --


@pytest.mark.parametrize(
    "code, token",
    [(ecodes.BTN_LEFT, "left"), (ecodes.BTN_RIGHT, "right"), (ecodes.BTN_MIDDLE, "middle")],
)
def test_each_mouse_button_is_captured(captured, code, token) -> None:
    events, recorder = captured
    recorder._handle(key_event(code, 1))
    assert [(e.kind, e.token) for e in events] == [("mouse_click", token)]


def test_both_halves_of_a_button_are_captured(captured) -> None:
    """The release used to be dropped, which is what made a drag unrecordable:
    a press and a release with movement between them is the whole gesture."""
    from macrokey.recorder.events import MOUSE_CLICK, MOUSE_RELEASE

    events, recorder = captured
    recorder._handle(key_event(ecodes.BTN_LEFT, 1))
    recorder._handle(key_event(ecodes.BTN_LEFT, 0))
    assert [event.kind for event in events] == [MOUSE_CLICK, MOUSE_RELEASE]


def test_the_alias_tuple_is_unwrapped() -> None:
    """The specific shape that used to be dropped."""
    assert isinstance(ecodes.BTN.get(ecodes.BTN_LEFT), (list, tuple))
    assert _token_for(ecodes.KEY_A) == "a"


def test_keypad_input_filter_uses_the_vendor_product_pair() -> None:
    from macrokey.recorder.evdev_source import KEYPAD_USB_IDS

    assert (0x1B4F, 0x9206) in KEYPAD_USB_IDS
    assert (0x1B4F, 0xBEEF) not in KEYPAD_USB_IDS


# ------------------------------------------------------------------- keyboard --


@pytest.mark.parametrize(
    "code, token",
    [
        (ecodes.KEY_A, "a"),
        (ecodes.KEY_1, "1"),
        (ecodes.KEY_ESC, "esc"),
        (ecodes.KEY_ENTER, "enter"),
        (ecodes.KEY_SPACE, "space"),
        (ecodes.KEY_LEFTCTRL, "ctrl"),
        (ecodes.KEY_LEFTSHIFT, "shift"),
        (ecodes.KEY_F5, "f5"),
        (ecodes.KEY_MINUS, "-"),
    ],
)
def test_keys_map_to_this_projects_vocabulary(code, token) -> None:
    assert _token_for(code) == token


def test_a_press_and_a_release_are_both_reported(captured) -> None:
    events, recorder = captured
    recorder._handle(key_event(ecodes.KEY_A, 1))
    recorder._handle(key_event(ecodes.KEY_A, 0))
    assert [e.kind for e in events] == ["key_down", "key_up"]


def test_auto_repeat_is_ignored(captured) -> None:
    """Value 2 is the keyboard repeating, not the person pressing again."""
    events, recorder = captured
    recorder._handle(key_event(ecodes.KEY_A, 1))
    for _ in range(20):
        recorder._handle(key_event(ecodes.KEY_A, 2))
    assert len(events) == 1


def test_shift_state_produces_the_shifted_character(captured) -> None:
    events, recorder = captured
    recorder._handle(key_event(ecodes.KEY_LEFTSHIFT, 1))
    recorder._handle(key_event(ecodes.KEY_1, 1))
    assert events[-1].char == "!"


def test_without_shift_the_plain_character_is_used(captured) -> None:
    events, recorder = captured
    recorder._handle(key_event(ecodes.KEY_1, 1))
    assert events[-1].char == "1"


# --------------------------------------------------------------------- wheel --


def test_scrolling_is_captured_with_its_sign(captured) -> None:
    events, recorder = captured
    recorder._handle(rel_event(ecodes.REL_WHEEL, -2))
    assert (events[0].kind, events[0].data) == ("scroll", (0, -2))


def test_pointer_deltas_are_held_until_flushed(captured) -> None:
    """REL motion is accumulated, not dropped. Emitting before flush would turn
    every kernel report into its own step; with no flush yet the buffer is empty.
    """
    events, recorder = captured
    recorder._handle(rel_event(ecodes.REL_X, 40))
    recorder._handle(rel_event(ecodes.REL_Y, -12))
    assert events == []
    recorder._flush_motion()
    assert len(events) == 1
    assert events[0].data == (40, -12)


# ------------------------------------------------------- motion accumulation --

pytest.importorskip("evdev")


def _fake(kind: int, code: int, value: int):
    from types import SimpleNamespace

    return SimpleNamespace(type=kind, code=code, value=value)


def _source():
    from macrokey.recorder.evdev_source import EvdevRecorder

    got = []
    return EvdevRecorder(got.append, capture_mouse=True), got


def test_a_report_stream_accumulates_into_one_move(monkeypatch) -> None:
    """The kernel appends EV_SYN to every mouse report. Treating that as "some
    other event happened" flushed after each one, so a drag came back as
    thousands of one-pixel steps instead of a single move."""
    from evdev import ecodes

    import macrokey.recorder.evdev_source as source_module

    source, got = _source()
    monkeypatch.setattr(source_module.time, "monotonic", lambda: 1.0)
    for _ in range(50):  # 50 reports, as a real mouse sends them
        source._handle(_fake(ecodes.EV_REL, ecodes.REL_X, 2))
        source._handle(_fake(ecodes.EV_REL, ecodes.REL_Y, -1))
        source._handle(_fake(ecodes.EV_SYN, ecodes.SYN_REPORT, 0))

    assert got == [], "nothing should be emitted while the pointer is still moving"
    source._flush_motion()
    assert len(got) == 1
    assert got[0].data == (100, -50)


def test_a_click_flushes_the_move_that_led_to_it() -> None:
    """Otherwise the click replays before the pointer has been moved."""
    from evdev import ecodes

    from macrokey.recorder.events import MOUSE_CLICK, MOUSE_MOVE

    source, got = _source()
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_X, 90))
    source._handle(_fake(ecodes.EV_SYN, ecodes.SYN_REPORT, 0))
    source._handle(_fake(ecodes.EV_KEY, ecodes.BTN_LEFT, 1))

    assert [event.kind for event in got] == [MOUSE_MOVE, MOUSE_CLICK]


def test_a_hand_resting_on_the_mouse_is_not_a_step() -> None:
    from evdev import ecodes

    source, got = _source()
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_X, 2))
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_Y, -1))
    source._flush_motion()
    assert got == []


def test_flushing_twice_emits_once() -> None:
    from evdev import ecodes

    source, got = _source()
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_X, 80))
    source._flush_motion()
    source._flush_motion()
    assert len(got) == 1


def test_a_long_motion_keeps_timing_slices_for_pointer_acceleration(monkeypatch) -> None:
    """Collapsing a whole gesture into one instant changes the desktop's mouse
    acceleration and therefore the replay distance. Long motion is compacted,
    but not past the point where all speed information disappears."""
    import macrokey.recorder.evdev_source as source_module

    source, got = _source()
    times = iter((1.00, 1.03, 1.06))
    monkeypatch.setattr(source_module.time, "monotonic", lambda: next(times))
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_X, 20))
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_X, 20))
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_X, 20))
    source._flush_motion()

    assert [event.data for event in got] == [(40, 0), (20, 0)]
    assert got[1].at - got[0].at >= 0.05


def test_a_small_move_a_click_follows_is_a_placement_not_noise() -> None:
    """The dead zone is for a hand resting on the mouse, and the rest timeout is
    where that is decided. Applying it to the flush a button triggers dropped a
    deliberate nudge instead, leaving the click a few counts from where it was
    made -- the same complaint as a move that stops short."""
    from evdev import ecodes

    from macrokey.recorder.events import MOUSE_CLICK, MOUSE_MOVE

    source, got = _source()
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_X, 4))  # under the dead zone
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_Y, 4))
    source._handle(_fake(ecodes.EV_SYN, ecodes.SYN_REPORT, 0))
    source._handle(_fake(ecodes.EV_KEY, ecodes.BTN_LEFT, 1))

    assert [event.kind for event in got] == [MOUSE_MOVE, MOUSE_CLICK]
    assert got[0].data == (4, 4)


def test_drift_before_a_keystroke_is_still_noise() -> None:
    """Typing is not aimed at the pointer, so the exception above stops at the
    mouse. Unfiltered here, every character typed with a hand resting on the
    mouse would become a step of its own."""
    from evdev import ecodes

    from macrokey.recorder.events import KEY_DOWN

    source, got = _source()
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_X, 3))
    source._handle(_fake(ecodes.EV_SYN, ecodes.SYN_REPORT, 0))
    source._handle(_fake(ecodes.EV_KEY, ecodes.KEY_A, 1))

    assert [event.kind for event in got] == [KEY_DOWN]


def test_a_real_move_before_a_keystroke_keeps_its_place() -> None:
    """Filtered is not the same as unordered: movement worth keeping still has
    to come out before the key it happened before."""
    from evdev import ecodes

    from macrokey.recorder.events import KEY_DOWN, MOUSE_MOVE

    source, got = _source()
    source._handle(_fake(ecodes.EV_REL, ecodes.REL_X, 90))
    source._handle(_fake(ecodes.EV_SYN, ecodes.SYN_REPORT, 0))
    source._handle(_fake(ecodes.EV_KEY, ecodes.KEY_A, 1))

    assert [event.kind for event in got] == [MOUSE_MOVE, KEY_DOWN]
