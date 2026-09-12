"""Key holds are press, wait, release -- not a tap.

Without preserve_key_timing every keystroke collapses to a click, so a
ten-second Shift+W recording replayed as a momentary jab. The checkbox that
opts into human hold timing is what game-style macros need; these tests are
the contract it owes.
"""

from __future__ import annotations

from macrokey.config.model import KEY_MODE_TYPE_IDS, Action
from macrokey.recorder.events import KEY_DOWN, KEY_UP, RawEvent
from macrokey.recorder.normalize import (
    MAX_DEVICE_DELAY_MS,
    compile_device_macro,
    normalize,
)


def down(token: str, at: float, *, char: str = "") -> RawEvent:
    return RawEvent(kind=KEY_DOWN, token=token, at=at, char=char)


def up(token: str, at: float) -> RawEvent:
    return RawEvent(kind=KEY_UP, token=token, at=at)


def modes(steps) -> list[str]:
    return [
        s.get("params", {}).get("mode", "click")
        for s in steps
        if s["type"] == "hotkey"
    ]


def test_default_path_still_collapses_a_hold_to_a_tap() -> None:
    steps = normalize(
        [down("shift", 1.0), down("w", 1.1, char="w"), up("w", 11.1), up("shift", 11.2)]
    )
    assert [s["type"] for s in steps] == ["hotkey"]
    assert steps[0]["params"]["hotkey"] == "shift+w"
    assert modes(steps) == ["click"]


def test_preserve_records_shift_and_w_as_separate_holds() -> None:
    steps = normalize(
        [down("shift", 1.0), down("w", 1.1, char="w"), up("w", 11.1), up("shift", 11.2)],
        preserve_key_timing=True,
        min_gap_ms=40,
    )
    assert modes(steps) == ["press", "press", "release", "release"]
    keys = [s["params"]["hotkey"] for s in steps if s["type"] == "hotkey"]
    assert keys == ["shift", "w", "w", "shift"]


def test_preserve_keeps_a_ten_second_hold() -> None:
    steps = normalize(
        [down("w", 1.0, char="w"), up("w", 11.0)],
        preserve_key_timing=True,
        min_gap_ms=40,
    )
    waited = sum(s["params"]["ms"] for s in steps if s["type"] == "delay")
    assert waited == 10_000


def test_compile_splits_long_holds_into_2550_ms_delay_steps() -> None:
    steps = normalize(
        [down("w", 1.0, char="w"), up("w", 11.0)],
        preserve_key_timing=True,
    )
    macro = compile_device_macro(steps)
    delays = [a.delay_ms for a in macro if a.kind == "delay"]
    assert delays
    assert all(d <= MAX_DEVICE_DELAY_MS for d in delays)
    assert sum(delays) == 10_000
    assert [a.mode for a in macro if a.kind == "key"] == ["press", "release"]


def test_key_press_and_release_round_trip_on_the_wire() -> None:
    press = Action(kind="key", hotkey="shift", mode="press")
    release = Action(kind="key", hotkey="w", mode="release")
    assert press.encode()[0] == KEY_MODE_TYPE_IDS["press"]
    assert release.encode()[0] == KEY_MODE_TYPE_IDS["release"]
    assert Action.decode(*press.encode()).mode == "press"
    assert Action.decode(*release.encode()).mode == "release"
    # Click keeps the historical ACT_KEY id so existing profiles stay identical.
    click = Action(kind="key", hotkey="a")
    assert click.encode()[0] == KEY_MODE_TYPE_IDS["click"] == 1


def test_describe_names_holds_clearly() -> None:
    assert "hold" in Action(kind="key", hotkey="w", mode="press").describe()
    assert "let go" in Action(kind="key", hotkey="w", mode="release").describe()


# ------------------------------------------------------- desktop auto-repeat --


def repeats(token: str, first: float, count: int, period: float) -> list[RawEvent]:
    """A held key as a desktop repeats it: press after press, no release."""
    return [down(token, first + step * period) for step in range(count)]


def test_a_held_key_is_one_press_however_often_the_desktop_repeats_it() -> None:
    """What put 190 `w press` records on a pad for one held key.

    evdev drops repeats at the source, but the pynput fallback hands them over
    as ordinary presses and nothing here caught them. The pad then replayed the
    burst: a keyboard that will not stop typing W.
    """
    events = [down("w", 1.0, char="w"), *repeats("w", 1.5, 200, 0.03), up("w", 11.0)]
    steps = normalize(events, preserve_key_timing=True, min_gap_ms=40)
    assert modes(steps) == ["press", "release"]
    assert sum(s["params"]["ms"] for s in steps if s["type"] == "delay") == 10_000


def test_repeats_of_a_held_modifier_are_dropped_too() -> None:
    """Shift arrived the same way, and a burst of shift presses is worse than a
    burst of letters: every one of them is a step the pad has to store."""
    events = [
        down("shift", 1.0),
        down("w", 1.1, char="w"),
        *repeats("shift", 1.6, 50, 0.03),
        *repeats("w", 1.6, 50, 0.03),
        up("w", 11.1),
        up("shift", 11.2),
    ]
    steps = normalize(events, preserve_key_timing=True, min_gap_ms=40)
    keys = [s["params"]["hotkey"] for s in steps if s["type"] == "hotkey"]
    assert keys == ["shift", "w", "w", "shift"]


def test_pressing_a_key_again_after_letting_go_is_still_two_presses() -> None:
    """The suppression is "already down", not "seen before" -- tapping the same
    key twice is the most ordinary thing a recording contains.
    """
    events = [
        down("w", 1.0, char="w"),
        up("w", 1.2),
        down("w", 1.6, char="w"),
        up("w", 1.8),
    ]
    steps = normalize(events, preserve_key_timing=True, min_gap_ms=40)
    assert modes(steps) == ["press", "release", "press", "release"]


def test_the_default_path_does_not_type_a_repeated_character() -> None:
    """Holding A recorded "aaaaaaa" on the pynput backend and "a" on evdev. The
    hold is not a request to type the letter fifty times.
    """
    events = [down("a", 1.0, char="a"), *repeats("a", 1.5, 20, 0.03), up("a", 3.0)]
    steps = normalize(events)
    assert [s["type"] for s in steps] == ["text"]
    assert steps[0]["params"]["text"] == "a"
