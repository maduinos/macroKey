"""Turns a raw capture into something worth replaying.

Replaying keystrokes exactly as typed is almost always wrong: it reproduces
human typing speed, splits a word into thirty events, and records the modifier
dance instead of the shortcut. This module folds all of that away.
"""

from __future__ import annotations

from typing import Any

from ..config import keycodes
from ..config.model import Action
from .events import KEY_DOWN, KEY_UP, MOUSE_CLICK, SCROLL, RawEvent

#: Gaps shorter than this are dropped rather than replayed.
DEFAULT_MIN_GAP_MS = 40
#: Gaps are rounded to this grid so a macro reads as intentional timing.
DELAY_QUANTUM_MS = 10
#: A pause longer than this is treated as the user thinking, not as timing.
MAX_DELAY_MS = 2000


def normalize(
    events: list[RawEvent],
    *,
    min_gap_ms: int = DEFAULT_MIN_GAP_MS,
    keep_delays: bool = True,
) -> list[dict[str, Any]]:
    """Raw events -> a list of host action specs."""
    steps: list[dict[str, Any]] = []
    held: list[str] = []
    text = ""
    last_at: float | None = None

    def flush_text() -> None:
        nonlocal text
        if text:
            steps.append({"type": "text", "params": {"text": text}})
            text = ""

    def add_delay(at: float) -> None:
        nonlocal last_at
        if last_at is not None and keep_delays:
            gap_ms = int(round((at - last_at) * 1000))
            if gap_ms >= min_gap_ms:
                quantized = min(MAX_DELAY_MS, _quantize(gap_ms))
                steps.append({"type": "delay", "params": {"ms": quantized}})
        last_at = at

    for event in events:
        if event.kind == KEY_UP:
            if event.token in held:
                held.remove(event.token)
            continue

        if event.kind == KEY_DOWN:
            if event.token in keycodes.MODIFIER_BITS:
                # Modifiers are never steps of their own; they decorate the key
                # that follows, which is what the user actually meant.
                if event.token not in held:
                    held.append(event.token)
                continue

            if held:
                flush_text()
                add_delay(event.at)
                combo = "+".join([*held, event.token])
                steps.append({"type": "hotkey", "params": {"hotkey": combo}})
                continue

            if event.printable:
                if not text:
                    add_delay(event.at)
                else:
                    last_at = event.at
                text += event.char
                continue

            flush_text()
            add_delay(event.at)
            steps.append({"type": "hotkey", "params": {"hotkey": event.token}})
            continue

        if event.kind == MOUSE_CLICK:
            # The button, not the position. Replaying the coordinates too would
            # mean a macro that clicks the right pixel only while nothing has
            # moved -- a different window position, a different resolution, a
            # second monitor, and it silently clicks something else. Buttons at
            # the current pointer are the part that stays true, and they are the
            # part the firmware can send by itself.
            flush_text()
            add_delay(event.at)
            steps.append({"type": "mouse_button", "params": {"button": event.token}})
            continue

        if event.kind == SCROLL:
            delta = event.data[1] if len(event.data) > 1 else 0
            if not delta:
                continue
            flush_text()
            add_delay(event.at)
            steps.append({"type": "mouse_wheel", "params": {"delta": int(delta)}})
            continue

    flush_text()
    return steps


def _quantize(milliseconds: int) -> int:
    return int(round(milliseconds / DELAY_QUANTUM_MS)) * DELAY_QUANTUM_MS


#: Commands that answer with a password prompt. What gets typed next is the
#: password, and it is typed into a terminal like anything else -- a recording
#: running at the time captures it verbatim and stores it in the profile.
PROMPTING_COMMANDS = ("sudo", "su", "ssh", "scp", "sftp", "passwd", "gpg", "mysql", "psql")


def find_likely_secrets(steps: list[dict[str, Any]]) -> list[int]:
    """Indices of text steps that are probably a password being answered.

    The shape is specific: a command that prompts, the Enter that submits it,
    then text. Requiring the Enter matters -- without it every word typed after
    the string "sudo" appeared anywhere would be suspected, and a macro that
    types `sudo apt update` and nothing else would lose its own command.

    This is a guess, and it is meant to be. Getting it wrong costs a step that
    has to be recorded again; getting it wrong the other way puts a password in
    a file, which is what already happened once.
    """
    secrets: list[int] = []
    # idle -> saw the command -> submitted it -> the next text is the answer.
    state = "idle"
    for index, step in enumerate(steps):
        kind = step.get("type")
        if kind == "delay":
            continue  # a pause is exactly what a prompt looks like

        if kind == "text":
            if state == "submitted":
                secrets.append(index)
                state = "idle"
                continue
            state = "saw-command" if _runs_prompting_command(step) else "idle"
            continue

        if kind == "hotkey" and step.get("params", {}).get("hotkey") == "enter":
            state = "submitted" if state == "saw-command" else "idle"
            continue

        state = "idle"
    return secrets


def _runs_prompting_command(step: dict[str, Any]) -> bool:
    """Whether this typed text runs something that will ask for a password.

    The command has to be what is being run, not merely mentioned: first word,
    or straight after a shell separator. Otherwise `echo "use sudo for this"`
    would arm the detector and eat the next thing typed.
    """
    words = step.get("params", {}).get("text", "").lower().split()
    separators = ("|", "&&", ";", "||", "&")
    return any(
        word in PROMPTING_COMMANDS and (position == 0 or words[position - 1] in separators)
        for position, word in enumerate(words)
    )


def redact_secrets(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drops probable passwords, returning the steps and how many went.

    Dropping rather than masking: a masked step would still be replayed, typing
    the wrong thing into a password prompt, and the point is that the secret
    never reaches storage at all.
    """
    secrets = set(find_likely_secrets(steps))
    if not secrets:
        return steps, 0
    kept = [step for index, step in enumerate(steps) if index not in secrets]
    return kept, len(secrets)


def summarize(steps: list[dict[str, Any]]) -> list[str]:
    """One readable line per step, shown before a recording is saved."""
    lines = []
    for step in steps:
        kind = step.get("type", "?")
        params = step.get("params", {})
        if kind == "hotkey":
            lines.append(f"press  {params.get('hotkey')}")
        elif kind == "text":
            value = params.get("text", "")
            preview = value if len(value) <= 40 else value[:37] + "..."
            lines.append(f"type   {preview!r}")
        elif kind == "delay":
            lines.append(f"wait   {params.get('ms')} ms")
        elif kind == "mouse_button":
            lines.append(f"click  {params.get('button')} button (where the pointer is)")
        elif kind == "mouse_wheel":
            delta = int(params.get("delta", 0))
            lines.append(f"scroll {'up' if delta > 0 else 'down'} {abs(delta)}")
        elif "note" in step:
            lines.append(f"skip   {step['note']}")
        else:
            lines.append(kind)
    return lines


def reduce_to_device_action(steps: list[dict[str, Any]]) -> Action | None:
    """Returns a firmware-native action when the recording is simple enough.

    A single shortcut belongs in EEPROM: it then fires with no PC app running
    and no round trip. Anything longer stays a host action.
    """
    meaningful = [step for step in steps if step.get("type") != "delay"]
    if len(meaningful) != 1:
        return None
    step = meaningful[0]
    if step.get("type") != "hotkey":
        return None

    hotkey = step.get("params", {}).get("hotkey", "")
    try:
        keycodes.parse_hotkey(hotkey)
    except keycodes.KeyParseError:
        return None
    return Action(kind="key", hotkey=hotkey)


#: A device macro step holds its delay in units of 10 ms in a single byte.
MAX_DEVICE_DELAY_MS = 255 * 10


def reduce_to_device_macro(
    steps: list[dict[str, Any]],
    *,
    max_steps: int = 32,
) -> list[Action] | None:
    """Compiles a whole recording into steps the firmware can replay itself.

    The firmware has always been able to run a stored sequence -- sixteen slots
    of up to thirty-two steps, with its own delay step -- but nothing ever built
    one, so every recording longer than a single shortcut became a host action
    and stopped working the moment the desktop app was not running. A keyboard
    macro with pauses is exactly what the sequence format is for.

    Returns None when a step has no on-device equivalent, which is the honest
    answer for mouse movement, long text and anything the host has to interpret.
    `max_steps` mirrors MK_MACRO_MAX_STEPS: the firmware stops replaying past it,
    so a macro that would be silently truncated is not a device macro at all.
    """
    if not steps:
        return None

    compiled: list[Action] = []
    for step in steps:
        kind = step.get("type")
        params = step.get("params", {})

        if kind == "delay":
            milliseconds = int(params.get("ms", 0))
            if milliseconds <= 0:
                continue
            # Longer pauses become several steps rather than being clipped: the
            # timing is the point of recording it in the first place.
            while milliseconds > 0:
                slice_ms = min(milliseconds, MAX_DEVICE_DELAY_MS)
                compiled.append(Action(kind="delay", delay_ms=slice_ms))
                milliseconds -= slice_ms
            continue

        if kind == "hotkey":
            hotkey = params.get("hotkey", "")
            try:
                keycodes.parse_hotkey(hotkey)
            except keycodes.KeyParseError:
                return None
            compiled.append(Action(kind="key", hotkey=hotkey))
            continue

        if kind == "consumer":
            usage = params.get("usage", "")
            if usage not in keycodes.CONSUMER_USAGES:
                return None
            compiled.append(Action(kind="consumer", usage=usage))
            continue

        if kind == "mouse_button":
            button = params.get("button", "left")
            try:
                compiled.append(Action(kind="mouse_button", button=button))
                compiled[-1].encode()
            except Exception:  # noqa: BLE001 - unknown button, keep it off the device
                return None
            continue

        if kind == "mouse_wheel":
            delta = int(params.get("delta", 0))
            # One signed byte on the wire; a bigger scroll becomes repeats.
            step = 127 if delta > 0 else -127
            while delta:
                slice_delta = step if abs(delta) > 127 else delta
                compiled.append(Action(kind="mouse_wheel", delta=int(slice_delta)))
                delta -= slice_delta
            continue

        # Typed text, clipboard and shell still need the host.
        return None

    if not compiled or len(compiled) > max_steps:
        return None
    return compiled
