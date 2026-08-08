"""Typed text as keys, so a recording of typing runs without the host.

Text was the thing that pushed a recording onto the host: the firmware presses
keys and has no "type this string" action. With the daemon off -- which is the
normal state, since nothing else needs it -- such a macro was bound, looked
bound, and did nothing.
"""

from __future__ import annotations

import pytest

from macrokey.recorder.normalize import expand_text_to_keys, reduce_to_device_macro


def text(value: str) -> dict:
    return {"type": "text", "params": {"text": value}}


def key(name: str) -> dict:
    return {"type": "hotkey", "params": {"hotkey": name}}


# ------------------------------------------------------------------ expansion --


def test_a_word_becomes_one_key_per_character() -> None:
    keys = expand_text_to_keys("ls")
    assert keys is not None
    assert [action.hotkey for action in keys] == ["l", "s"]


def test_space_is_a_named_key() -> None:
    """Passing " " to the key parser is an empty hotkey, not a space."""
    keys = expand_text_to_keys("a b")
    assert keys is not None
    assert [action.hotkey for action in keys] == ["a", "space", "b"]


def test_uppercase_becomes_shift_plus_the_key() -> None:
    """The parser lower-cases what it is handed, so the shift has to be explicit."""
    keys = expand_text_to_keys("Hi")
    assert keys is not None
    assert [action.hotkey for action in keys] == ["shift+h", "i"]


@pytest.mark.parametrize("character", list("!@#$%^&*()_+-=[]{};':\",./<>?`~|\\"))
def test_every_printable_symbol_expands(character: str) -> None:
    keys = expand_text_to_keys(character)
    assert keys is not None, f"{character!r} did not expand"
    keys[0].encode()


def test_a_realistic_command_expands_and_encodes() -> None:
    keys = expand_text_to_keys("sudo apt update")
    assert keys is not None
    assert len(keys) == 15
    for action in keys:
        action.encode()


@pytest.mark.parametrize(
    "value", ["안녕", "café", "a\tb", "a\nb", ""], ids=["hangul", "accent", "tab", "newline", "empty"]
)
def test_what_the_keypad_has_no_keys_for_stays_on_the_host(value: str) -> None:
    assert expand_text_to_keys(value) is None


# --------------------------------------------------------------- on the device --


def test_the_terminal_macro_that_used_to_need_a_daemon_now_fits() -> None:
    """Open a terminal, type a command, press enter -- the motivating case."""
    macro = reduce_to_device_macro(
        [key("ctrl+alt+t"), {"type": "delay", "params": {"ms": 850}}, text("sudo apt update"), key("enter")]
    )
    assert macro is not None
    assert len(macro) == 18
    assert macro[0].hotkey == "ctrl+alt+t"
    assert macro[-1].hotkey == "enter"


def test_text_past_the_step_limit_stays_on_the_host() -> None:
    """Expanding it would be silently truncated by the firmware mid-word."""
    assert reduce_to_device_macro([text("sudo apt update && sudo apt upgrade -y")]) is None


def test_one_unexpandable_character_sends_the_whole_macro_to_the_host() -> None:
    """Half a macro on the device would type half the text and stop."""
    assert reduce_to_device_macro([key("ctrl+alt+t"), text("안녕")]) is None


def test_expansion_does_not_disturb_the_other_step_kinds() -> None:
    macro = reduce_to_device_macro(
        [text("hi"), {"type": "mouse_button", "params": {"button": "left"}}, key("enter")]
    )
    assert macro is not None
    assert [action.kind for action in macro] == ["key", "key", "mouse_button", "key"]
