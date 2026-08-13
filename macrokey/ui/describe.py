"""Turning profile objects into the words the window shows.

Apart from the widgets because none of it needs Qt to be reasoned about, and
because the wording is the part most often argued with.
"""

from __future__ import annotations

import os

from ..config import Action, Profile
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


def describe_binding(profile: Profile, action: Action) -> str:
    """What this key does, said the way someone using the pad would say it.

    The grid used to show `action.describe()`, which speaks in the wire format's
    terms -- "sequence 1" -- and told you nothing about what pressing the key
    would produce.
    """
    if action.kind == "none":
        return "nothing"
    if action.kind == "sequence":
        macros = profile.device_macros
        steps = macros[action.slot] if action.slot < len(macros) else []
        # Counted the way it reads, not the way it is stored. A typed line is
        # one text action, so "1 step" would be true and useless; the number
        # someone wants is how much of the recording there is.
        typed = sum(len(step.text) for step in steps if step.kind == "text")
        others = sum(1 for step in steps if step.kind not in ("text", "delay"))
        parts = []
        if typed:
            parts.append(f"{typed} characters")
        if others:
            parts.append(f"{others} key{'s' if others != 1 else ''}")
        detail = " + ".join(parts) or "empty"
        return f"recording, {detail} (on the keypad)"
    return action.describe()


def nothing_captured_hint() -> str:
    """Why a recording can come back empty, when that has a known cause."""
    usable, reason = Recorder.available()
    if not usable:
        return f"Nothing was captured. {reason}"
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return (
            "Nothing was captured. On Wayland, prefer being in the `input` group "
            "so capture uses evdev (every window). Without it, only X11 windows "
            "are visible to the fallback recorder."
        )
    return "Nothing was captured. Hold a pad key for 3 seconds, do the thing, hold again to finish."


#: A typed run at least this long is worth pointing at before it is stored.
#: Real macros type short things -- a command, a name, a snippet; passwords and
#: pasted tokens are what long unbroken runs usually are.
