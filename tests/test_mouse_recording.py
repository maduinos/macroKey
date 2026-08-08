"""Mouse capture, and the ways it can quietly poison a macro.

Recording stops on a button, so the click that stops it is the last thing the
listener sees. Without care that click becomes the macro's final step, and every
replay ends by clicking wherever the Stop button used to be.
"""

from __future__ import annotations

import pytest

from macrokey.recorder.events import KEY_DOWN, MOUSE_CLICK, SCROLL, RawEvent
from macrokey.recorder.normalize import normalize, reduce_to_device_macro, summarize
from macrokey.recorder.recorder import Recorder


def click(token: str, at: float, x: int = 0, y: int = 0) -> RawEvent:
    return RawEvent(kind=MOUSE_CLICK, token=token, at=at, data=(x, y))


def scroll(dy: int, at: float, x: int = 0, y: int = 0) -> RawEvent:
    return RawEvent(kind=SCROLL, token="scroll", at=at, data=(0, dy))


def press(token: str, at: float, char: str = "") -> RawEvent:
    return RawEvent(kind=KEY_DOWN, token=token, char=char, at=at)


# --------------------------------------------------------------- normalising --


def test_a_click_becomes_a_replayable_step() -> None:
    """It used to be recorded as an annotated no-op, so nothing was replayed."""
    steps = normalize([click("left", 1.0)])
    assert steps == [{"type": "mouse_button", "params": {"button": "left"}}]


def test_the_button_is_kept_and_the_position_is_not() -> None:
    steps = normalize([click("right", 1.0, x=1234, y=567)])
    assert steps[0]["params"] == {"button": "right"}
    assert "x" not in steps[0]["params"] and "y" not in steps[0]["params"]


def test_scrolling_is_kept_with_its_direction() -> None:
    steps = normalize([scroll(-3, 1.0)])
    assert steps == [{"type": "mouse_wheel", "params": {"delta": -3}}]


def test_a_scroll_of_zero_is_dropped() -> None:
    assert normalize([scroll(0, 1.0)]) == []


def test_timing_between_mouse_and_keyboard_is_preserved() -> None:
    steps = normalize([click("left", 1.0), press("a", 1.3, "a")])
    assert [step["type"] for step in steps] == ["mouse_button", "delay", "text"]
    assert steps[1]["params"]["ms"] == 300


def test_the_summary_says_where_a_click_lands() -> None:
    lines = summarize(normalize([click("left", 1.0)]))
    assert "pointer" in lines[0]


# ------------------------------------------------------------ onto the device --


def test_a_mouse_recording_still_fits_on_the_keypad() -> None:
    """Buttons and wheel are firmware actions, so this needs no host."""
    macro = reduce_to_device_macro(normalize([click("left", 1.0), scroll(-2, 1.5)]))
    assert macro is not None
    assert [action.kind for action in macro] == ["mouse_button", "delay", "mouse_wheel"]


def test_a_big_scroll_is_split_to_fit_one_signed_byte() -> None:
    macro = reduce_to_device_macro([{"type": "mouse_wheel", "params": {"delta": 300}}])
    assert macro is not None
    assert sum(action.delta for action in macro) == 300
    assert all(abs(action.delta) <= 127 for action in macro)


def test_an_unknown_button_is_not_forced_onto_the_device() -> None:
    assert reduce_to_device_macro([{"type": "mouse_button", "params": {"button": "x9"}}]) is None


def test_short_typed_text_mixed_with_mouse_fits_on_the_device() -> None:
    """Text expands to key presses, so this no longer needs the host at all."""
    recording = normalize([press("h", 1.0, "h"), press("i", 1.05, "i"), click("left", 1.5)])
    assert any(step["type"] == "text" for step in recording)
    macro = reduce_to_device_macro(recording)
    assert macro is not None
    assert [action.kind for action in macro] == ["key", "key", "delay", "mouse_button"]


def test_text_the_keypad_has_no_keys_for_still_needs_the_host() -> None:
    recording = normalize([press("\uc548", 1.0, "\uc548"), click("left", 1.5)])
    assert reduce_to_device_macro(recording) is None


# ------------------------------------------------------ the stop-click problem --


@pytest.fixture
def recorder() -> Recorder:
    device = Recorder(capture_mouse=True)
    device.ignore_click_region = (100, 200, 400, 300)  # x, y, w, h
    return device


@pytest.mark.parametrize(
    "point",
    [(100, 200), (499, 499), (300, 350)],
    ids=["top-left corner", "bottom-right inside", "middle"],
)
def test_a_click_on_the_recorder_window_is_not_part_of_the_macro(recorder, point) -> None:
    recorder._events.clear()
    recorder._on_click(point[0], point[1], type("B", (), {"name": "left"}), True)
    assert recorder._events == [], f"click at {point} was recorded"


@pytest.mark.parametrize(
    "point",
    [(99, 200), (100, 199), (500, 400), (100, 500), (0, 0)],
    ids=["left of", "above", "right edge", "below", "origin"],
)
def test_a_click_anywhere_else_is_part_of_the_macro(recorder, point) -> None:
    recorder._events.clear()
    recorder._on_click(point[0], point[1], type("B", (), {"name": "left"}), True)
    assert len(recorder._events) == 1, f"click at {point} was dropped"


def test_scrolling_over_the_recorder_window_is_also_ignored(recorder) -> None:
    recorder._events.clear()
    recorder._on_scroll(300, 350, 0, -2)
    assert recorder._events == []


def test_with_no_region_set_every_click_counts() -> None:
    device = Recorder(capture_mouse=True)
    device._on_click(300, 350, type("B", (), {"name": "left"}), True)
    assert len(device._events) == 1


def test_button_release_is_not_a_second_click(recorder) -> None:
    recorder._events.clear()
    recorder._on_click(10, 10, type("B", (), {"name": "left"}), True)
    recorder._on_click(10, 10, type("B", (), {"name": "left"}), False)
    assert len(recorder._events) == 1


# ------------------------------------------------------------------ stop key --


def test_esc_is_recordable_when_no_stop_key_is_set() -> None:
    """Esc used to end the recording, so a macro could never contain it."""
    device = Recorder()
    assert device.stop_key is None
    device._on_press(type("K", (), {"name": "esc"}))
    assert [event.token for event in device._events] == ["esc"]


def test_a_stop_key_still_works_when_one_is_asked_for() -> None:
    device = Recorder(stop_key="esc")
    device.recording = True
    device._on_press(type("K", (), {"name": "esc"}))
    assert device.recording is False
    assert device._events == []
