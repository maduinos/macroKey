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
            # A recorded click would replay at whatever coordinates the pointer
            # happens to be at, which is almost never what was meant. It is kept
            # as an annotated no-op so the user sees it in the summary and can
            # decide, rather than silently getting a macro that misclicks.
            flush_text()
            add_delay(event.at)
            steps.append(
                {
                    "type": "noop",
                    "params": {},
                    "note": f"mouse {event.token} click (not replayed)",
                }
            )
            continue

        if event.kind == SCROLL:
            continue

    flush_text()
    return steps


def _quantize(milliseconds: int) -> int:
    return int(round(milliseconds / DELAY_QUANTUM_MS)) * DELAY_QUANTUM_MS


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
