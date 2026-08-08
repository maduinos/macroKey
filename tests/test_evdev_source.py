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


def test_button_release_is_not_a_second_click(captured) -> None:
    events, recorder = captured
    recorder._handle(key_event(ecodes.BTN_LEFT, 1))
    recorder._handle(key_event(ecodes.BTN_LEFT, 0))
    assert len(events) == 1


def test_the_alias_tuple_is_unwrapped() -> None:
    """The specific shape that used to be dropped."""
    assert isinstance(ecodes.BTN.get(ecodes.BTN_LEFT), (list, tuple))
    assert _token_for(ecodes.KEY_A) == "a"


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


def test_pointer_movement_is_not_recorded(captured) -> None:
    """Positions are deliberately not replayed, so they are not captured."""
    events, recorder = captured
    recorder._handle(rel_event(ecodes.REL_X, 40))
    recorder._handle(rel_event(ecodes.REL_Y, -12))
    assert events == []
