"""The host and the firmware each build a profile from their own source.

Nothing checks that at build time, and the two are only ever compared on a real
device. These keep the host side honest: the binary layout, the round trip, and
the defaults that have to match `Profile::writeDefaults`.
"""

from __future__ import annotations

import pytest

from macrokey.config import binary
from macrokey.config.model import (
    KEY_COUNT,
    LAYER_COLORS,
    LAYER_COUNT,
    Action,
    default_profile,
)

GESTURES = ("tap", "double", "hold")


def test_encoded_profile_is_the_size_the_firmware_expects() -> None:
    assert len(binary.encode_profile(default_profile())) == binary.PROFILE_SIZE


def test_round_trip_preserves_every_bound_slot() -> None:
    profile = default_profile()
    restored = binary.decode_profile(binary.encode_profile(profile))
    for layer in range(LAYER_COUNT):
        for key in range(KEY_COUNT):
            for gesture in GESTURES:
                before = profile.action(layer, key, gesture)
                after = restored.action(layer, key, gesture)
                assert after.kind == before.kind, f"L{layer} key{key} {gesture}"
                assert after.describe() == before.describe()


def test_round_trip_is_byte_stable() -> None:
    """Encode, decode, encode again: the bytes must not drift."""
    once = binary.encode_profile(default_profile())
    twice = binary.encode_profile(binary.decode_profile(once))
    assert once == twice


def test_layer_colours_round_trip() -> None:
    restored = binary.decode_profile(binary.encode_profile(default_profile()))
    assert [layer.keys[0].color for layer in restored.layers] == list(LAYER_COLORS)


def test_brightness_round_trips() -> None:
    profile = default_profile()
    profile.brightness = 137
    assert binary.decode_profile(binary.encode_profile(profile)).brightness == 137


# --------------------------------------------------------------------- defaults --


def test_every_layer_above_the_base_can_be_reached() -> None:
    """A layer with no way into it is a layer that does not exist.

    Layers 2 and 3 shipped with colours defined and nothing bound to enter them.
    """
    profile = default_profile()
    reachable = {
        profile.action(layer, key, gesture).layer
        for layer in range(LAYER_COUNT)
        for key in range(KEY_COUNT)
        for gesture in GESTURES
        if profile.action(layer, key, gesture).kind in ("layer_momentary", "layer_toggle")
    }
    assert reachable == set(range(1, LAYER_COUNT))


def test_base_layer_binds_every_key_on_tap() -> None:
    profile = default_profile()
    for key in range(KEY_COUNT):
        assert profile.action(0, key, "tap").kind == "key"


def test_no_double_tap_is_bound_by_default() -> None:
    """Arming double-tap on a key delays every tap of it, so the default is none."""
    profile = default_profile()
    bound = [
        (layer, key)
        for layer in range(LAYER_COUNT)
        for key in range(KEY_COUNT)
        if profile.action(layer, key, "double").kind != "none"
    ]
    assert bound == []


def test_layer_colour_count_matches_layer_count() -> None:
    assert len(LAYER_COLORS) == LAYER_COUNT


def test_base_layer_is_a_valid_colour() -> None:
    for value in LAYER_COLORS:
        assert len(value) == 6
        int(value, 16)  # raises if it is not hex


# ----------------------------------------------------------------------- limits --


def test_action_rejects_a_layer_outside_the_range() -> None:
    action = Action(kind="layer_momentary", layer=99)
    # Encoding clamps rather than raising, so the device can never be told to
    # switch to a layer it does not have.
    _, value, _, _ = action.encode()
    assert value == LAYER_COUNT - 1


@pytest.mark.parametrize("hotkey", ["ctrl+alt+shift+1", "ctrl+c", "f5", "a"])
def test_common_hotkeys_encode(hotkey: str) -> None:
    Action(kind="key", hotkey=hotkey).encode()


def test_an_unknown_hotkey_is_rejected_before_it_reaches_the_device() -> None:
    """The editor validates on OK so a bad key cannot reach a push."""
    with pytest.raises((KeyError, ValueError)):
        Action(kind="key", hotkey="ctrl+notakey").encode()
