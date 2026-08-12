"""Re-recording into a key must reuse the storage the old recording held.

This is the bug that quietly undid the whole device-first design. Correcting a
macro is the ordinary thing to do with one, and every correction left the old
slot full: sixteen of them filled all sixteen slots with steps nothing pointed
at, and from then on every recording failed to fit -- so the pad stopped getting
new macros with nothing saying why.
"""

from __future__ import annotations

import pytest

from macrokey.app import MacroKeyApp
from macrokey.config.model import (
    MACRO_RECORD_CAPACITY,
    MACRO_SLOTS,
    Action,
    ProfileError,
    default_profile,
    macro_records,
)
from macrokey.recorder.normalize import reduce_to_device_macro

#: A recording that has to become a macro rather than a single action.
RECORDING = [
    {"type": "text", "params": {"text": "hello world"}},
    {"type": "delay", "params": {"ms": 100}},
    {"type": "hotkey", "params": {"hotkey": "enter"}},
]


class FakeRecorder:
    @staticmethod
    def device_action(steps):
        return None  # force the macro path

    @staticmethod
    def device_macro(steps):
        return reduce_to_device_macro(steps)


def fake_app() -> MacroKeyApp:
    app = MacroKeyApp.__new__(MacroKeyApp)
    app.profile = default_profile()
    app.recorder = FakeRecorder()
    app._status_callbacks = []
    app._event_callbacks = []
    return app


def used_slots(app) -> int:
    return sum(1 for macro in app.profile.device_macros if macro)


def used_records(app) -> int:
    return sum(macro_records(macro) for macro in app.profile.device_macros)


# ------------------------------------------------------------ the actual bug --


def test_re_recording_the_same_key_reuses_its_slot() -> None:
    app = fake_app()
    for attempt in range(MACRO_SLOTS * 3):
        where = app.assign_recording(RECORDING, 0, "tap")
        assert "keypad" in where, f"failed to store on attempt {attempt + 1}"
        assert used_slots(app) == 1, f"{used_slots(app)} slots after {attempt + 1} recordings"


def test_the_record_budget_does_not_creep() -> None:
    app = fake_app()
    app.assign_recording(RECORDING, 0, "tap")
    after_one = used_records(app)
    for _ in range(20):
        app.assign_recording(RECORDING, 0, "tap")
    assert used_records(app) == after_one


def test_unsupported_steps_are_rejected_rather_than_stored_on_the_host() -> None:
    app = fake_app()
    app.profile.set_action(0, "tap", Action(kind="key", hotkey="ctrl+alt+shift+1"))
    with pytest.raises(ProfileError, match="does not fit"):
        app.assign_recording([{"type": "shell", "params": {"command": "ls"}}], 0, "tap")
    # Rejected assign must not wipe the key that was already bound.
    assert app.profile.action(0, "tap").kind == "key"
    assert app.profile.action(0, "tap").hotkey == "ctrl+alt+shift+1"


def test_every_key_still_gets_its_own_slot() -> None:
    """Reclaiming must not confuse "replaced" with "some other key's"."""
    app = fake_app()
    for key in range(8):
        app.assign_recording(RECORDING, key, "tap")
    assert used_slots(app) == 8
    slots = {app.profile.action(key, "tap").slot for key in range(8)}
    assert len(slots) == 8


def test_tap_and_double_on_one_key_do_not_evict_each_other() -> None:
    app = fake_app()
    app.assign_recording(RECORDING, 0, "tap")
    app.assign_recording(RECORDING, 0, "double")
    assert used_slots(app) == 2
    assert app.profile.action(0, "tap").kind == "sequence"
    assert app.profile.action(0, "double").kind == "sequence"


# ------------------------------------------------------------------ the sweep --


def test_reclaim_repairs_a_profile_that_already_leaked() -> None:
    """Profiles written before the fix carry the wreckage, so the sweep has to
    clear it rather than only avoid adding to it."""
    profile = default_profile()
    profile.device_macros = [[Action(kind="key", hotkey="a")] for _ in range(MACRO_SLOTS)]
    profile.set_action(0, "tap", Action(kind="sequence", slot=4))

    freed_slots = profile.reclaim_storage()
    assert freed_slots == MACRO_SLOTS - 1
    assert [index for index, m in enumerate(profile.device_macros) if m] == [4]


def test_reclaim_keeps_what_is_still_referenced() -> None:
    app = fake_app()
    app.assign_recording(RECORDING, 2, "tap")
    slot = app.profile.action(2, "tap").slot
    app.profile.reclaim_storage()
    assert app.profile.device_macros[slot]


def test_a_full_pad_rejects_rather_than_falling_back_to_the_host() -> None:
    app = fake_app()
    app.profile.device_macros = [[Action(kind="key", hotkey="a")] * MACRO_RECORD_CAPACITY]
    app.profile.set_action(7, "tap", Action(kind="sequence", slot=0))
    app.profile.set_action(0, "tap", Action(kind="key", hotkey="ctrl+alt+shift+1"))
    with pytest.raises(ProfileError, match="does not fit"):
        app.assign_recording(RECORDING, 0, "tap")
    assert app.profile.action(0, "tap").hotkey == "ctrl+alt+shift+1"


def test_re_recording_reuses_the_same_slot_without_wiping_on_failure() -> None:
    app = fake_app()
    app.assign_recording(RECORDING, 0, "tap")
    slot = app.profile.action(0, "tap").slot
    with pytest.raises(ProfileError, match="does not fit"):
        app.assign_recording([{"type": "shell", "params": {"command": "ls"}}], 0, "tap")
    assert app.profile.action(0, "tap").kind == "sequence"
    assert app.profile.action(0, "tap").slot == slot
    assert app.profile.device_macros[slot]
