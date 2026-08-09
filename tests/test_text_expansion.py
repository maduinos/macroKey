"""Typed text as device text runs, so a recording of typing runs without the host.

Text was the thing that pushed a recording onto the host: the firmware presses
keys and had no "type this string" action. With the daemon off -- which is the
normal state, since nothing else needs it -- such a macro was bound, looked
bound, and did nothing.

Expanding it to one key action per character fixed that and created the next
problem: three bytes a letter, against a region that held 273 of them, so a
single command line was most of the pad. A text run is a header record plus one
record per three characters, which is what these tests are about.
"""

from __future__ import annotations

import pytest

from macrokey.config.model import MACRO_MAX_RECORDS, TEXT_RUN_MAX, macro_records
from macrokey.recorder.normalize import reduce_to_device_macro, text_to_runs


def text(value: str) -> dict:
    return {"type": "text", "params": {"text": value}}


def key(name: str) -> dict:
    return {"type": "hotkey", "params": {"hotkey": name}}


# ----------------------------------------------------------------- text runs --


def test_a_word_is_one_run_not_one_action_per_character() -> None:
    runs = text_to_runs("ls")
    assert runs is not None
    assert [action.kind for action in runs] == ["text"]
    assert runs[0].text == "ls"


def test_a_run_costs_a_header_plus_a_record_per_three_characters() -> None:
    """The whole point. "sudo apt update" is 15 characters: a header and five
    records, against the 15 key actions it used to be."""
    runs = text_to_runs("sudo apt update")
    assert runs is not None
    assert macro_records(runs) == 1 + 5


@pytest.mark.parametrize("length", [1, 2, 3, 4, 38, 100, 255])
def test_the_record_count_matches_what_is_actually_emitted(length: int) -> None:
    """`record_count` is what the capacity check trusts, so it may not disagree
    with `records` -- a slot allocated from one and filled from the other would
    overrun whatever came after it."""
    action = text_to_runs("a" * length)[0]
    assert action.record_count() == len(action.records())


@pytest.mark.parametrize("character", list("!@#$%^&*()_+-=[]{};':\",./<>?`~|\\"))
def test_every_printable_symbol_survives_a_run(character: str) -> None:
    runs = text_to_runs(character)
    assert runs is not None, f"{character!r} was refused"
    assert runs[0].text == character


def test_case_is_carried_as_written() -> None:
    """The firmware types the byte, so shift is its problem rather than a
    separate action the way `shift+h` used to be."""
    runs = text_to_runs("Hi There")
    assert runs is not None
    assert runs[0].text == "Hi There"


def test_text_longer_than_one_run_is_split_rather_than_refused() -> None:
    """A run's length is one byte. Splitting keeps long text on the device."""
    runs = text_to_runs("a" * (TEXT_RUN_MAX + 10))
    assert runs is not None
    assert [len(action.text) for action in runs] == [TEXT_RUN_MAX, 10]


@pytest.mark.parametrize(
    "value",
    ["안녕", "café", "a\tb", "a\nb", ""],
    ids=["hangul", "accent", "tab", "newline", "empty"],
)
def test_what_the_keypad_cannot_type_stays_on_the_host(value: str) -> None:
    assert text_to_runs(value) is None


# ------------------------------------------------------------- on the device --


def test_the_terminal_macro_that_used_to_need_a_daemon_now_fits() -> None:
    """Open a terminal, type a command, press enter -- the motivating case."""
    macro = reduce_to_device_macro(
        [
            key("ctrl+alt+t"),
            {"type": "delay", "params": {"ms": 850}},
            text("sudo apt update"),
            key("enter"),
        ]
    )
    assert macro is not None
    assert [action.kind for action in macro] == ["key", "delay", "text", "key"]
    assert macro[0].hotkey == "ctrl+alt+t"
    assert macro[-1].hotkey == "enter"
    # Nine records, where one action per character made it eighteen steps.
    assert macro_records(macro) == 9


def test_an_ordinary_command_costs_a_third_of_what_it_used_to() -> None:
    line = "sudo apt update && sudo apt upgrade -y"
    macro = reduce_to_device_macro([text(line)])
    assert macro is not None
    assert macro_records(macro) == 14  # was 38, one key action per character


def test_a_slot_now_holds_hundreds_of_characters() -> None:
    """The ceiling that matters is records, so text buys roughly three times the
    length. Comfortably past a shell command, which is what this is for."""
    longest = "x" * 700
    macro = reduce_to_device_macro(
        [text(longest[offset : offset + TEXT_RUN_MAX]) for offset in range(0, 700, TEXT_RUN_MAX)]
    )
    assert macro is not None
    assert macro_records(macro) <= MACRO_MAX_RECORDS


def test_text_past_the_record_limit_stays_on_the_host() -> None:
    """Storing it would be truncated by the firmware mid-word. Two full runs
    are 86 records each, leaving 83 for a third: 255 + 255 + 246 characters."""
    assert reduce_to_device_macro([text("a" * 756)]) is not None
    assert reduce_to_device_macro([text("a" * 757)]) is None


def test_one_untypable_character_sends_the_whole_macro_to_the_host() -> None:
    """Half a macro on the device would type half the text and stop."""
    assert reduce_to_device_macro([key("ctrl+alt+t"), text("안녕")]) is None


def test_text_does_not_disturb_the_other_step_kinds() -> None:
    macro = reduce_to_device_macro(
        [text("hi"), {"type": "mouse_button", "params": {"button": "left"}}, key("enter")]
    )
    assert macro is not None
    assert [action.kind for action in macro] == ["text", "mouse_button", "key"]
