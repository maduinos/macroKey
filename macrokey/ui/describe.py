"""Turning profile objects into the words the window shows.

Apart from the widgets because none of it needs Qt to be reasoned about, and
because the wording is the part most often argued with.
"""

from __future__ import annotations

import os

from ..config import Action, Profile

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
    terms -- "host 3", "sequence 1" -- and told you nothing about what pressing
    the key would produce.
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
    if action.kind == "host":
        spec = profile.host_actions.get(action.token)
        if spec is None:
            return f"missing host action {action.token}"
        return f"recording: {spec.describe()} (needs this computer)"
    return action.describe()



def daemon_running() -> bool:
    """Whether anything is actually listening on the daemon's state socket.

    Existence is not enough: the socket file outlives the process that made it,
    so a stopped daemon leaves one behind and a check on the path alone reports
    a daemon that is not there. Connecting is the only answer that means
    anything.
    """
    import socket as socket_module

    from ..led import default_socket_path

    if not hasattr(socket_module, "AF_UNIX"):
        return False
    try:
        path = default_socket_path()
        if not path.exists():
            return False
        with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            probe.connect(str(path))
        return True
    except OSError:
        return False


def nothing_captured_hint() -> str:
    """Why a recording can come back empty, when that has a known cause.

    pynput falls back to its X11 backend under Wayland, where it only sees
    input going to XWayland clients. Typing into a native Wayland window is
    invisible to it, and the recording ends up empty with no explanation.
    """
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return (
            "Nothing was captured. On Wayland, input capture only sees X11 "
            "windows, so typing into most applications is invisible to it. "
            "Recording into a terminal started under XWayland does work."
        )
    return "Nothing was captured. Press Start recording, do the thing, then Stop."


#: A typed run at least this long is worth pointing at before it is stored.
#: Real macros type short things -- a command, a name, a snippet; passwords and
#: pasted tokens are what long unbroken runs usually are.
