"""Keeping password prompts out of stored recordings.

This is not hypothetical. A recording of "open a terminal and run apt update"
captured the sudo password that followed and wrote it into profile.json, which
was world-readable at the time. The shape is specific and recognisable: a
command that prompts, the Enter that submits it, then whatever was typed next.
"""

from __future__ import annotations

import pytest

from macrokey.recorder.normalize import find_likely_secrets, redact_secrets


def text(value: str) -> dict:
    return {"type": "text", "params": {"text": value}}


def key(name: str) -> dict:
    return {"type": "hotkey", "params": {"hotkey": name}}


def wait(ms: int = 100) -> dict:
    return {"type": "delay", "params": {"ms": ms}}


# ------------------------------------------------------------------ detection --


def test_the_recording_that_actually_leaked_is_caught() -> None:
    steps = [
        key("ctrl+alt+t"),
        wait(850),
        text("sudo apt update"),
        wait(100),
        key("enter"),
        wait(780),
        text("s3cret"),
        wait(260),
        key("enter"),
    ]
    assert find_likely_secrets(steps) == [6]


@pytest.mark.parametrize(
    "command",
    ["sudo apt upgrade", "su -", "ssh box", "passwd", "sudo -i", "gpg --decrypt f"],
)
def test_every_prompting_command_arms_the_filter(command: str) -> None:
    steps = [text(command), key("enter"), text("s3cret")]
    assert find_likely_secrets(steps) == [2]


def test_a_pause_before_the_prompt_does_not_break_the_chain() -> None:
    """Waiting is exactly what a password prompt looks like."""
    steps = [text("sudo ls"), wait(50), key("enter"), wait(2000), text("s3cret")]
    assert find_likely_secrets(steps) == [4]


def test_a_command_after_a_shell_separator_still_counts() -> None:
    steps = [text("cd /tmp && sudo ls"), key("enter"), text("s3cret")]
    assert find_likely_secrets(steps) == [2]


# --------------------------------------------------------------- false alarms --


def test_a_macro_that_only_runs_the_command_keeps_its_command() -> None:
    """The most common real macro. Losing this step would break the macro."""
    steps = [text("sudo apt update"), key("enter")]
    assert find_likely_secrets(steps) == []
    assert redact_secrets(steps)[1] == 0


def test_the_word_mentioned_in_passing_does_not_arm_it() -> None:
    steps = [text('echo "use sudo for this"'), key("enter"), text("next thing")]
    assert find_likely_secrets(steps) == []


def test_text_without_a_submitting_enter_is_not_an_answer() -> None:
    """Without Enter the command never ran, so nothing prompted."""
    steps = [text("sudo apt update"), text("more typing")]
    assert find_likely_secrets(steps) == []


def test_arming_does_not_drift_past_an_unrelated_step() -> None:
    steps = [text("sudo ls"), key("enter"), key("ctrl+c"), text("safe")]
    assert find_likely_secrets(steps) == []


def test_only_the_first_text_after_the_prompt_is_taken() -> None:
    steps = [text("sudo ls"), key("enter"), text("s3cret"), key("enter"), text("ls -la")]
    assert find_likely_secrets(steps) == [2]


def test_an_ordinary_recording_is_untouched() -> None:
    steps = [key("ctrl+c"), wait(200), key("ctrl+v")]
    assert redact_secrets(steps) == (steps, 0)


# ---------------------------------------------------------------- redaction --


def test_the_secret_is_dropped_not_masked() -> None:
    """A masked step would still replay, typing the wrong thing at a prompt."""
    steps = [text("sudo ls"), key("enter"), text("s3cret")]
    kept, count = redact_secrets(steps)
    assert count == 1
    assert all("s3cret" not in step.get("params", {}).get("text", "") for step in kept)
    assert len(kept) == 2


def test_the_rest_of_the_recording_survives_in_order() -> None:
    steps = [key("ctrl+alt+t"), text("sudo ls"), key("enter"), text("s3cret"), key("enter")]
    kept, _ = redact_secrets(steps)
    assert [step.get("params") for step in kept] == [
        {"hotkey": "ctrl+alt+t"},
        {"text": "sudo ls"},
        {"hotkey": "enter"},
        {"hotkey": "enter"},
    ]


def test_redaction_is_stable_when_run_twice() -> None:
    steps = [text("sudo ls"), key("enter"), text("s3cret")]
    once, first = redact_secrets(steps)
    twice, second = redact_secrets(once)
    assert first == 1
    assert second == 0
    assert twice == once
