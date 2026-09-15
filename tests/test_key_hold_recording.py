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


def test_real_timing_keeps_short_holds_and_cumulative_duration() -> None:
    events = [down('w', 1.0)]
    for index in range(1, 21):
        events.append((down if index % 2 else up)('a', 1 + index * .016))
    events.append(up('w', 1.336))
    steps = normalize(events, preserve_key_timing=True, min_gap_ms=100)
    assert sum(s['params']['ms'] for s in steps if s['type'] == 'delay') == 340
    short = normalize([down('w', 1), up('w', 1.03)], preserve_key_timing=True)
    assert short[1] == {'type': 'delay', 'params': {'ms': 30}}


def test_real_timing_ignores_orphan_releases_and_sorts_late_events() -> None:
    steps = normalize(
        [up('a', .5), down('w', 1), up('w', 2), down('shift', 1.5)],
        preserve_key_timing=True,
    )
    assert [s['params']['hotkey'] for s in steps if s['type'] == 'hotkey'] == [
        'w', 'shift', 'w'
    ]
    assert sum(s['params']['ms'] for s in steps if s['type'] == 'delay') == 1000


def test_stop_closes_held_keys_at_stop_time_before_backend_cleanup(monkeypatch) -> None:
    from macrokey.recorder.recorder import Recorder

    recorder = Recorder(preserve_key_timing=True)
    recorder.backend = 'evdev'
    recorder.recording = True
    recorder._record(down('shift', 1))
    recorder._record(down('w', 1.1))

    class Backend:
        def stop(self):
            # Backend cleanup must not extend capture beyond the stop request.
            recorder._record(up('w', 12))

    recorder._evdev = Backend()
    monkeypatch.setattr('macrokey.recorder.recorder.time.monotonic', lambda: 11)
    events = recorder.stop()
    assert [(e.token, e.at) for e in events[-2:]] == [('w', 11), ('shift', 11)]
    steps = recorder.steps(events)
    macro = compile_device_macro(steps)
    assert sum(a.delay_ms for a in macro if a.kind == 'delay') == 10000
    assert modes(steps) == ['press', 'press', 'release', 'release']
    recorder._record(down('a', 10))  # Late callback after stop cannot alter capture.
    assert recorder.stop() == events


def test_stop_key_uses_event_time_not_later_cleanup_time(monkeypatch) -> None:
    from macrokey.recorder.recorder import Recorder

    recorder = Recorder(preserve_key_timing=True, stop_key='esc')
    recorder.backend = 'evdev'
    recorder._record(down('w', 1))
    recorder._record(down('esc', 3))
    monkeypatch.setattr('macrokey.recorder.recorder.time.monotonic', lambda: 10)
    steps = recorder.steps(recorder.stop())
    assert sum(s['params']['ms'] for s in steps if s['type'] == 'delay') == 2000
    assert modes(steps) == ['press', 'release']


def test_pynput_release_flushes_motion_before_releasing_key(monkeypatch) -> None:
    from macrokey.recorder.events import MOUSE_MOVE
    from macrokey.recorder.recorder import Recorder

    recorder = Recorder(preserve_key_timing=True)
    recorder.backend = 'pynput'
    recorder._record(down('shift', 1))
    recorder._pynput_motion_started_at = 1.1
    recorder._pynput_pending_dx = 100
    monkeypatch.setattr('macrokey.recorder.recorder._describe_key', lambda _: ('shift', ''))
    monkeypatch.setattr('macrokey.recorder.recorder.time.monotonic', lambda: 1.2)
    recorder._on_release(object())
    assert [e.kind for e in recorder._events] == [KEY_DOWN, MOUSE_MOVE, KEY_UP]
    steps = recorder.steps()
    assert [s['type'] for s in steps] == ['hotkey', 'delay', 'mouse_move', 'delay', 'hotkey']


def test_real_timing_can_still_explicitly_disable_delays() -> None:
    steps = normalize([down('w', 1), up('w', 11)],
                      preserve_key_timing=True, keep_delays=False)
    assert [s['type'] for s in steps] == ['hotkey', 'hotkey']


def test_a_new_recording_resets_the_previous_stop_boundary(monkeypatch) -> None:
    from macrokey.recorder.recorder import Recorder

    class Backend:
        def __init__(self, callback, **kwargs):
            self.callback = callback

        def start(self):
            self.callback(down('w', 20))

        def stop(self):
            pass

    recorder = Recorder(preserve_key_timing=True)
    recorder.backend = 'evdev'
    recorder._record(down('a', 1))
    monkeypatch.setattr('macrokey.recorder.recorder.time.monotonic', lambda: 2)
    recorder.stop()
    monkeypatch.setattr('macrokey.recorder.recorder.evdev_source.available', lambda: (True, ''))
    monkeypatch.setattr('macrokey.recorder.recorder.evdev_source.EvdevRecorder', Backend)
    recorder.start()
    monkeypatch.setattr('macrokey.recorder.recorder.time.monotonic', lambda: 21)
    events = recorder.stop()
    assert [(e.kind, e.token, e.at) for e in events] == [
        (KEY_DOWN, 'w', 20), (KEY_UP, 'w', 21)
    ]
