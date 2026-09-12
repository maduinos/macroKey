"""Turning profile objects into the words the window shows.

Apart from the widgets because none of it needs Qt to be reasoned about, and
because the wording is the part most often argued with.
"""

from __future__ import annotations

import os

from ..config import Action, Profile
from ..config.model import MIN_MACRO_REPEAT, repeated_shortcut, text_speed_shown
from ..i18n import tr
from ..recorder.recorder import Recorder

SECRET_TEXT_LENGTH = 12


def longest_typed_run(steps) -> int:
    return max(
        (
            len(step.get("params", {}).get("text", ""))
            for step in steps
            if step.get("type") == "text"
        ),
        default=0,
    )


def macro_pass_ms(profile: Profile, slot: int) -> int:
    """Roughly how long one pass of this recording takes.

    The pauses are the recording's own timing, and a mouse slice is replayed
    *over* the pause that follows it rather than after it, so the delays are
    already the whole clock for everything but typing. Typing is paced by the
    profile's own rate, which is the other term.

    Rough on purpose: it exists to turn "255" into "about half an hour" before
    someone presses a key and finds out. A count or two of drift changes nothing
    about that.
    """
    macros = profile.device_macros
    if slot >= len(macros):
        return 0
    steps = macros[slot]
    typed = sum(len(step.text) for step in steps if step.kind == "text")
    delays = sum(step.delay_ms for step in steps if step.kind == "delay")
    return delays + typed * text_speed_shown(profile.text_speed_ms)


def format_duration(milliseconds: int) -> str:
    """A length someone can picture, at the coarsest unit that still says it."""
    seconds = round(milliseconds / 1000)
    if seconds < 60:
        return tr("about {seconds} seconds").format(seconds=max(1, seconds))
    minutes = round(seconds / 60)
    if minutes < 60:
        return tr("about {minutes} minutes").format(minutes=max(1, minutes))
    hours = minutes / 60
    return tr("about {hours} hours").format(hours=f"{hours:.1f}".rstrip("0").rstrip("."))


def describe_binding(profile: Profile, action: Action) -> str:
    """What this key does, said the way someone using the pad would say it.

    The grid used to show `action.describe()`, which speaks in the wire format's
    terms -- "sequence 1" -- and told you nothing about what pressing the key
    would produce.
    """
    if action.kind == "none":
        return tr("nothing")
    if action.kind == "sequence":
        macros = profile.device_macros
        steps = macros[action.slot] if action.slot < len(macros) else []
        # A shortcut that was wrapped so it could repeat is still a shortcut.
        # "recording, 1 key (on the keypad)" is true of it and tells nobody
        # which key it sends.
        shortcut = repeated_shortcut(steps)
        if shortcut is not None:
            said = shortcut.describe()
            if action.repeat > MIN_MACRO_REPEAT:
                said += tr(", repeated {repeat} times ({duration})").format(
                    repeat=action.repeat,
                    duration=format_duration(macro_pass_ms(profile, action.slot) * action.repeat),
                )
            return said
        # Counted the way it reads, not the way it is stored. A typed line is
        # one text action, so "1 step" would be true and useless; the number
        # someone wants is how much of the recording there is.
        typed = sum(len(step.text) for step in steps if step.kind == "text")
        others = sum(1 for step in steps if step.kind not in ("text", "delay"))
        parts = []
        if typed:
            parts.append(tr("{typed} characters").format(typed=typed))
        if others:
            # Two keys rather than one plural rule: Korean has no plural -s, so
            # the choice has to be a lookup, not an appended letter.
            singular_or_plural = "{others} key" if others == 1 else "{others} keys"
            parts.append(tr(singular_or_plural).format(others=others))
        detail = " + ".join(parts) or tr("empty")
        said = tr("recording, {detail} (on the keypad)").format(detail=detail)
        if action.repeat > MIN_MACRO_REPEAT:
            # The count and what it costs in time, together: "x255" is the
            # setting, and the minutes are the part that decides whether it is
            # the setting anyone wanted.
            said += tr(", repeated {repeat} times ({duration})").format(
                repeat=action.repeat,
                duration=format_duration(macro_pass_ms(profile, action.slot) * action.repeat),
            )
        return said
    return action.describe()


def nothing_captured_hint() -> str:
    """Why a recording can come back empty, when that has a known cause."""
    usable, reason = Recorder.available()
    if not usable:
        return tr("Nothing was captured. {reason}").format(reason=reason)
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return tr(
            "Nothing was captured. On Wayland, prefer being in the `input` group "
            "so capture uses evdev (every window). Without it, only X11 windows "
            "are visible to the fallback recorder."
        )
    return tr(
        "Nothing was captured. Hold a pad key for 3 seconds, do the thing, "
        "hold again to finish."
    )


#: A typed run at least this long is worth pointing at before it is stored.
#: Real macros type short things -- a command, a name, a snippet; passwords and
#: pasted tokens are what long unbroken runs usually are.
