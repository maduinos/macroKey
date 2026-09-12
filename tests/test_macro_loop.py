"""Replaying one recording several times from a single press.

A "real timing" recording is expensive in records -- a ten second hold is a
press, four delays and a release, and the pad holds 308 records in total -- so
repeating one by recording it again does not fit. 42 passes fill a slot; 255
passes do not fit on the device at all. The loop count is what makes that
recording repeatable, and it costs nothing: it rides in a byte of the keymap
entry that was already there and already zero.

These tests are the host half. `test_firmware_agreement.py` drives the same
bytes through the firmware's own replay.
"""

from __future__ import annotations

import pytest

from macrokey.config import binary, model
from macrokey.config.model import (
    MAX_MACRO_REPEAT,
    MIN_MACRO_REPEAT,
    SHORTCUT_REPEAT_GAP_MS,
    Action,
    ProfileError,
)


def sequence(**kwargs) -> Action:
    return Action(kind="sequence", slot=2, **kwargs)


# ------------------------------------------------------------------- the byte --


def test_a_repeat_rides_in_the_spare_byte_of_the_keymap_entry() -> None:
    """`b` and `c` were both written as zero, so no storage is spent on this."""
    type_id, slot, repeat, reserved = sequence(repeat=100).encode()
    assert (type_id, slot, repeat) == (model.ACTION_TYPE_IDS["sequence"], 2, 100)
    assert reserved == 0, "c stays reserved rather than widening the count"


def test_one_pass_is_stored_as_zero_not_one() -> None:
    """The "Profile differs" trap, and the reason `text_speed_stored` exists.

    A pad that has never been told a loop count has zero in that byte. The app
    compares profiles as raw bytes, so storing 1 for the same behaviour would
    report a difference that is not one -- a prompt on the first connect of
    every install, about a setting nobody touched.
    """
    assert sequence().encode()[2] == 0
    assert sequence(repeat=1).encode()[2] == 0


def test_a_stored_zero_reads_back_as_one_pass() -> None:
    """Every profile written before loop counts existed holds zero here."""
    assert Action.decode(model.ACTION_TYPE_IDS["sequence"], 2, 0, 0).repeat == 1


@pytest.mark.parametrize("repeat", [MIN_MACRO_REPEAT, 2, 99, MAX_MACRO_REPEAT])
def test_a_repeat_survives_the_wire(repeat: int) -> None:
    assert Action.decode(*sequence(repeat=repeat).encode()).repeat == repeat


@pytest.mark.parametrize("repeat", [0, -1, MAX_MACRO_REPEAT + 1])
def test_a_repeat_outside_what_the_byte_holds_is_refused(repeat: int) -> None:
    with pytest.raises(ProfileError):
        sequence(repeat=repeat)


# ------------------------------------------------------------------- storage --


def test_a_repeat_survives_a_saved_profile() -> None:
    assert Action.from_dict(sequence(repeat=42).to_dict()).repeat == 42


def test_one_pass_is_not_written_to_the_file() -> None:
    """`to_dict` emits only what differs from the default, and this is default."""
    assert "repeat" not in sequence().to_dict()


def test_a_repeat_survives_the_whole_blob() -> None:
    profile = model.default_profile()
    profile.set_action(0, "tap", sequence(repeat=255))
    profile.set_action(1, "double", sequence(repeat=1))

    read_back = binary.decode_profile(binary.encode_profile(profile))
    assert read_back.action(0, "tap").repeat == 255
    assert read_back.action(1, "double").repeat == 1


def test_tap_and_double_carry_separate_counts() -> None:
    """Both gestures are their own four-byte keymap entry, so the count is per
    binding rather than per key -- and per key rather than per macro slot, which
    is why the same recording can be repeated differently from two keys.
    """
    profile = model.default_profile()
    profile.set_action(4, "tap", sequence(repeat=3))
    profile.set_action(4, "double", sequence(repeat=200))

    read_back = binary.decode_profile(binary.encode_profile(profile))
    assert read_back.action(4, "tap").repeat == 3
    assert read_back.action(4, "double").repeat == 200
    assert read_back.action(4, "tap").slot == read_back.action(4, "double").slot


def test_a_profile_with_no_repeats_encodes_exactly_as_it_used_to() -> None:
    """The bytes a pad already holds must not move. Nothing here is new storage,
    so a profile that asks for no repeats has to be bit-identical to one built
    before the field existed.
    """
    profile = model.default_profile()
    profile.set_action(0, "tap", Action(kind="sequence", slot=1))
    blob = bytearray(binary.encode_profile(profile))

    # The same profile as the old encoder wrote it: `b` and `c` hard zero.
    address = binary.KEYMAP_OFFSET
    assert bytes(blob[address : address + 4]) == bytes(
        (model.ACTION_TYPE_IDS["sequence"], 1, 0, 0)
    )


# -------------------------------------------------------------------- wording --


def test_the_grid_says_how_many_times_and_how_long() -> None:
    """255 is a number. Half an hour is the part that decides whether it is the
    number anyone wanted, so the two are said together.
    """
    from macrokey.ui.describe import describe_binding

    profile = model.default_profile()
    profile.device_macros = [[Action(kind="delay", delay_ms=2000)]]
    said = describe_binding(profile, Action(kind="sequence", slot=0, repeat=60))
    assert "60" in said
    assert "2 minutes" in said or "2분" in said


def test_a_single_pass_says_nothing_extra() -> None:
    from macrokey.ui.describe import describe_binding

    profile = model.default_profile()
    profile.device_macros = [[Action(kind="delay", delay_ms=2000)]]
    assert "repeat" not in describe_binding(profile, Action(kind="sequence", slot=0))


# --------------------------------------------------------------------- editor --

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QSpinBox  # noqa: E402

from macrokey.app import MacroKeyApp  # noqa: E402
from macrokey.ui.slot_dialog import SlotDialog  # noqa: E402


@pytest.fixture
def app(monkeypatch, tmp_path):
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    instance = MacroKeyApp()
    instance.profile.device_macros = [[Action(kind="delay", delay_ms=1000)]]
    yield instance
    instance.close()


def spin(dialog) -> QSpinBox | None:
    return dialog.findChild(QSpinBox)


def test_the_editor_shows_the_repeat_even_with_nothing_to_repeat(app) -> None:
    """Hiding the row made the feature unfindable. A pad out of the box holds
    eight shortcuts and no recordings, so every key opened this window with the
    row absent and nothing anywhere saying what would bring it back.
    """
    dialog = SlotDialog(None, app, 7, "double")  # empty binding

    assert spin(dialog) is not None, "the row is gone again"
    assert not spin(dialog).isEnabled(), "there is nothing here to repeat"
    assert dialog.repeat_cost.text().strip(), "disabled and silent says nothing"


def test_a_shortcut_can_be_repeated_too(app) -> None:
    """A shortcut's four bytes are type, modifiers, keycode and flags -- nothing
    spare for a count -- so the app wraps it in a one-shortcut macro, which is
    the only thing the pad knows how to replay more than once.
    """
    app.profile.set_action(0, "double", Action(kind="key", hotkey="alt+ctrl+t"))
    dialog = SlotDialog(None, app, 0, "double")

    assert spin(dialog).isEnabled(), "a shortcut has nowhere to put a count, not no reason to"
    spin(dialog).setValue(10)
    dialog._use_repeat()

    bound = app.profile.action(0, "double")
    assert bound.kind == "sequence"
    assert bound.repeat == 10
    assert app.profile.device_macros[bound.slot] == [
        Action(kind="key", hotkey="alt+ctrl+t"),
        Action(kind="delay", delay_ms=SHORTCUT_REPEAT_GAP_MS),
    ]


def test_a_wrapped_shortcut_still_reads_as_the_shortcut(app) -> None:
    """"recording, 1 key (on the keypad)" is true of it and tells nobody which
    key it sends.
    """
    from macrokey.ui.describe import describe_binding

    app.profile.set_action(0, "double", Action(kind="key", hotkey="alt+ctrl+t"))
    app.set_repeat(0, "double", 10)

    said = describe_binding(app.profile, app.profile.action(0, "double"))
    assert "alt+ctrl+t" in said
    assert "10" in said


def test_going_back_to_once_unwraps_the_shortcut(app) -> None:
    """Otherwise a slot is left holding a macro that exists to run once."""
    app.profile.set_action(0, "double", Action(kind="key", hotkey="alt+ctrl+t"))
    app.set_repeat(0, "double", 10)
    slot = app.profile.action(0, "double").slot

    app.set_repeat(0, "double", 1)

    assert app.profile.action(0, "double") == Action(kind="key", hotkey="alt+ctrl+t")
    assert app.profile.device_macros[slot] == [], "the slot was not reclaimed"


def test_a_real_recording_is_never_unwrapped(app) -> None:
    """The shape check must not mistake a recording for a wrapped shortcut."""
    app.profile.device_macros = [[Action(kind="key", hotkey="a"),
                                  Action(kind="delay", delay_ms=300)]]
    app.profile.set_action(0, "tap", Action(kind="sequence", slot=0, repeat=4))

    app.set_repeat(0, "tap", 1)
    assert app.profile.action(0, "tap").kind == "sequence"
    assert app.profile.device_macros[0] != []


def test_an_empty_key_cannot_be_repeated(app) -> None:
    with pytest.raises(ProfileError):
        app.set_repeat(7, "double", 5)


def test_the_repeat_turns_on_for_a_recording(app) -> None:
    app.profile.set_action(1, "tap", Action(kind="sequence", slot=0))
    dialog = SlotDialog(None, app, 1, "tap")

    assert spin(dialog).isEnabled()
    assert dialog.set_repeat.isEnabled()


def test_the_double_gesture_gets_its_own_count(app) -> None:
    """The count rides in the keymap entry, and tap and double are separate
    entries -- so this is independent per gesture rather than per key.
    """
    app.profile.set_action(3, "tap", Action(kind="sequence", slot=0))
    app.profile.set_action(3, "double", Action(kind="sequence", slot=0))

    editor = SlotDialog(None, app, 3, "double")
    spin(editor).setValue(7)
    editor._use_repeat()

    assert app.profile.action(3, "double").repeat == 7
    assert app.profile.action(3, "tap").repeat == 1, "tap followed double"


def test_setting_a_repeat_reads_the_binding_again(app) -> None:
    """Recording is driven by the pad on a background thread, and its signals
    arrive while this modal dialog is up. A count written back over the action
    the window opened with would restore the slot the recording had replaced.
    """
    app.profile.set_action(0, "tap", Action(kind="sequence", slot=0))
    dialog = SlotDialog(None, app, 0, "tap")

    # A recording lands on the same key while the window is open.
    app.profile.set_action(0, "tap", Action(kind="sequence", slot=5))

    spin(dialog).setValue(9)
    dialog._use_repeat()
    assert app.profile.action(0, "tap") == Action(kind="sequence", slot=5, repeat=9)
