"""Where a recording ends up decides whether the pad works away from this PC.

The firmware has always had a sequence player -- sixteen slots, its own delay
step -- and nothing ever put anything in it, so every recording longer than one
shortcut became a host action and quietly depended on a daemon running.
"""

from __future__ import annotations

import pytest

from macrokey.config.model import MACRO_SLOTS, MACRO_STEP_CAPACITY
from macrokey.recorder.normalize import (
    MAX_DEVICE_DELAY_MS,
    reduce_to_device_action,
    reduce_to_device_macro,
)


def steps(*items):
    """Ints are delays in ms, strings are hotkeys."""
    return [
        {"type": "delay", "params": {"ms": item}}
        if isinstance(item, int)
        else {"type": "hotkey", "params": {"hotkey": item}}
        for item in items
    ]


# ------------------------------------------------------------------ on device --


def test_a_single_shortcut_is_a_plain_action_not_a_macro() -> None:
    """One shortcut needs no slot; spending one would waste storage."""
    assert reduce_to_device_action(steps("ctrl+c")) is not None


def test_a_recording_with_timing_compiles_to_device_steps() -> None:
    macro = reduce_to_device_macro(steps("ctrl+c", 200, "ctrl+v"))
    assert macro is not None
    assert [action.kind for action in macro] == ["key", "delay", "key"]
    assert macro[1].delay_ms == 200


def test_leading_and_trailing_structure_is_preserved_in_order() -> None:
    macro = reduce_to_device_macro(steps("ctrl+c", 100, "ctrl+v", 150, "ctrl+s"))
    assert macro is not None
    assert [a.describe() for a in macro] == [
        "ctrl+c",
        "wait 100 ms",
        "ctrl+v",
        "wait 150 ms",
        "ctrl+s",
    ]


def test_a_long_pause_is_split_rather_than_clipped() -> None:
    """A device delay step holds 10 ms units in one byte; timing is the point."""
    macro = reduce_to_device_macro(steps("ctrl+s", 3000, "f5"))
    assert macro is not None
    waited = sum(a.delay_ms for a in macro if a.kind == "delay")
    assert waited == 3000
    assert all(a.delay_ms <= MAX_DEVICE_DELAY_MS for a in macro if a.kind == "delay")


def test_zero_length_delays_are_dropped() -> None:
    macro = reduce_to_device_macro(steps("ctrl+c", 0, "ctrl+v"))
    assert macro is not None
    assert [a.kind for a in macro] == ["key", "key"]


def test_every_compiled_step_encodes() -> None:
    macro = reduce_to_device_macro(steps("ctrl+alt+shift+1", 500, "f5"))
    assert macro is not None
    for action in macro:
        action.encode()


# --------------------------------------------------------------- host fallback --


@pytest.mark.parametrize(
    "recording",
    [
        [{"type": "clipboard_image", "params": {"path": "x.png"}}],
        [{"type": "shell", "params": {"command": "ls"}}],
        [{"type": "text", "params": {"text": "\uc548\ub155"}}],
    ],
    ids=["clipboard", "shell", "text with no keys for it"],
)
def test_steps_the_firmware_cannot_replay_stay_on_the_host(recording) -> None:
    """Short ASCII text now expands to keys; these are what genuinely cannot."""
    assert reduce_to_device_macro(recording) is None


def test_an_unparseable_hotkey_is_not_forced_onto_the_device() -> None:
    assert reduce_to_device_macro(steps("ctrl+notakey")) is None


def test_an_empty_recording_is_not_a_macro() -> None:
    assert reduce_to_device_macro([]) is None


def test_a_recording_past_the_firmware_step_limit_stays_on_the_host() -> None:
    """MK_MACRO_MAX_STEPS stops replay part way, which is worse than not trying."""
    too_long = steps(*(["ctrl+c"] * 40))
    assert reduce_to_device_macro(too_long, max_steps=32) is None


# ------------------------------------------------------------ slot allocation --


class FakeApp:
    """Just the slot bookkeeping from MacroKeyApp."""

    def __init__(self) -> None:
        from macrokey.config.model import default_profile

        self.profile = default_profile()

    _claim_macro_slot = None  # bound below


def claim(app, macro):
    from macrokey.app import MacroKeyApp

    return MacroKeyApp._claim_macro_slot(app, macro)


def test_slots_are_handed_out_in_order() -> None:
    app = FakeApp()
    assert claim(app, [object()]) == 0
    assert claim(app, [object()]) == 1


def test_storage_is_shared_so_a_free_slot_is_not_enough() -> None:
    """Sixteen slots share one byte budget; a free slot with no room is no room."""
    app = FakeApp()
    assert claim(app, [object()] * MACRO_STEP_CAPACITY) == 0
    assert claim(app, [object()]) is None


def test_running_out_of_slots_returns_none_rather_than_raising() -> None:
    app = FakeApp()
    for _ in range(MACRO_SLOTS):
        assert claim(app, [object()]) is not None
    assert claim(app, [object()]) is None
