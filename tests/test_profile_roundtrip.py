"""The host and the firmware each build a profile from their own source.

Nothing checks that at build time, and the two are only ever compared on a real
device. These keep the host side honest: the binary layout, the round trip, and
the defaults that have to match `Profile::writeDefaults`.
"""

from __future__ import annotations

import pytest

from macrokey.config import binary
from macrokey.config.model import (
    DEFAULT_RESTING_COLOR,
    KEY_COUNT,
    Action,
    ProfileError,
    default_profile,
)

GESTURES = ("tap", "double", "hold")


def test_encoded_profile_is_the_size_the_firmware_expects() -> None:
    assert len(binary.encode_profile(default_profile())) == binary.PROFILE_SIZE


def test_round_trip_preserves_every_bound_slot() -> None:
    profile = default_profile()
    restored = binary.decode_profile(binary.encode_profile(profile))
    for key in range(KEY_COUNT):
        for gesture in GESTURES:
            before = profile.action(key, gesture)
            after = restored.action(key, gesture)
            assert after.kind == before.kind, f"key {key + 1} {gesture}"
            assert after.describe() == before.describe()


def test_round_trip_is_byte_stable() -> None:
    """Encode, decode, encode again: the bytes must not drift."""
    once = binary.encode_profile(default_profile())
    twice = binary.encode_profile(binary.decode_profile(once))
    assert once == twice


def test_the_resting_colour_round_trips() -> None:
    restored = binary.decode_profile(binary.encode_profile(default_profile()))
    assert restored.resting_color == DEFAULT_RESTING_COLOR


def test_a_chosen_resting_colour_round_trips() -> None:
    profile = default_profile()
    profile.resting_color = "ff8800"
    assert binary.decode_profile(binary.encode_profile(profile)).resting_color == "ff8800"


def test_brightness_round_trips() -> None:
    profile = default_profile()
    profile.brightness = 137
    assert binary.decode_profile(binary.encode_profile(profile)).brightness == 137


# --------------------------------------------------------------------- defaults --


def test_every_key_is_bound_on_tap() -> None:
    profile = default_profile()
    for key in range(KEY_COUNT):
        assert profile.action(key, "tap").kind == "key"


def test_no_double_tap_is_bound_by_default() -> None:
    """Arming double-tap on a key delays every tap of it, so the default is none."""
    profile = default_profile()
    assert [key for key in range(KEY_COUNT) if profile.action(key, "double").kind != "none"] == []


@pytest.mark.parametrize("hotkey", ["ctrl+alt+shift+1", "ctrl+c", "f5", "a"])
def test_common_hotkeys_encode(hotkey: str) -> None:
    Action(kind="key", hotkey=hotkey).encode()


def test_an_unknown_hotkey_is_rejected_before_it_reaches_the_device() -> None:
    """The editor validates on OK so a bad key cannot reach a push."""
    with pytest.raises((KeyError, ValueError)):
        Action(kind="key", hotkey="ctrl+notakey").encode()


# --------------------------------------------------- hold belongs to recording --


def test_hold_is_reported_by_the_firmware_but_not_editable() -> None:
    """Holding a key alone for three seconds opens the recorder. The gesture
    still exists on the wire -- the firmware emits it at 400 ms on the way --
    but nothing may be bound to it."""
    from macrokey.config.model import EDITABLE_GESTURES, GESTURES

    assert "hold" in GESTURES
    assert "hold" not in EDITABLE_GESTURES


def test_binding_hold_is_refused_outright() -> None:
    """Hiding it in the editor was not enough: a hold binding written in the
    session still reached the pad, and only got cleared on the next load. It
    fires at 400 ms while the recorder opens at 3 s -- one press, two unrelated
    things -- so the write itself is what has to fail."""
    profile = default_profile()
    with pytest.raises(ProfileError, match="reserved"):
        profile.set_action(3, "hold", Action(kind="key", hotkey="ctrl+alt+t"))


def test_clearing_hold_is_still_allowed() -> None:
    """Sanitising an old profile has to be able to write the empty action."""
    profile = default_profile()
    profile.set_action(3, "hold", Action())
    assert profile.action(3, "hold").kind == "none"


def test_hold_has_no_room_in_the_keymap_at_all() -> None:
    """It is not stored, so a device blob cannot carry one back."""
    restored = binary.decode_profile(binary.encode_profile(default_profile()))
    assert all(restored.action(key, "hold").kind == "none" for key in range(KEY_COUNT))


def test_tap_and_double_survive_the_same_path() -> None:
    profile = default_profile()
    profile.set_action(2, "tap", Action(kind="key", hotkey="ctrl+c"))
    profile.set_action(2, "double", Action(kind="key", hotkey="ctrl+v"))

    restored = binary.decode_profile(binary.encode_profile(profile))
    assert restored.action(2, "tap").hotkey == "ctrl+c"
    assert restored.action(2, "double").hotkey == "ctrl+v"


def test_no_key_ships_with_a_hold_binding() -> None:
    profile = default_profile()
    for key in range(KEY_COUNT):
        assert profile.action(key, "hold").kind == "none"


# ------------------------------------------------ the macro region, in full --


def test_the_profile_uses_the_whole_eeprom() -> None:
    """The region was written down by hand twice -- 480, then 768 -- and each
    time left bytes unused. It is derived from the EEPROM size now."""
    assert binary.PROFILE_SIZE == binary.EEPROM_SIZE == 1024


def test_a_macro_can_start_past_the_old_one_byte_offset() -> None:
    """The index held an 8-bit record offset, so no slot could begin past 255
    however much room the region had. Offsets are implied by the counts before
    a slot now, which is both smaller and unable to disagree with the region."""
    from macrokey.config.model import MACRO_RECORD_CAPACITY

    assert MACRO_RECORD_CAPACITY > 255, "otherwise this proves nothing"

    profile = default_profile()
    # 200 + 60 puts the third slot at step 260, which no 8-bit offset can name.
    profile.device_macros = [
        [Action(kind="key", hotkey="a")] * 200,
        [Action(kind="key", hotkey="b")] * 60,
        [Action(kind="key", hotkey="z"), Action(kind="key", hotkey="y")],
    ]

    restored = binary.decode_profile(binary.encode_profile(profile))
    assert [len(m) for m in restored.device_macros] == [200, 60, 2]
    assert [step.hotkey for step in restored.device_macros[2]] == ["z", "y"]


def _fill(total: int) -> list[list[Action]]:
    """`total` single-record actions, spread over as many slots as it takes."""
    from macrokey.config.model import MACRO_MAX_RECORDS

    macros = []
    while total:
        take = min(total, MACRO_MAX_RECORDS)
        macros.append([Action(kind="key", hotkey="a")] * take)
        total -= take
    return macros


def test_filling_the_region_exactly_round_trips() -> None:
    from macrokey.config.model import MACRO_RECORD_CAPACITY

    profile = default_profile()
    profile.device_macros = _fill(MACRO_RECORD_CAPACITY)

    restored = binary.decode_profile(binary.encode_profile(profile))
    assert sum(len(m) for m in restored.device_macros) == MACRO_RECORD_CAPACITY


def test_one_record_past_the_region_is_refused() -> None:
    from macrokey.config.model import MACRO_RECORD_CAPACITY

    profile = default_profile()
    profile.device_macros = _fill(MACRO_RECORD_CAPACITY + 1)

    with pytest.raises(ProfileError, match="exhausted"):
        binary.encode_profile(profile)
