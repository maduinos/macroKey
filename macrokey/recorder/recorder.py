"""Captures real input so it can be replayed from a macro key."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from .events import KEY_DOWN, KEY_UP, MOUSE_CLICK, SCROLL, RawEvent
from .normalize import (
    DEFAULT_MIN_GAP_MS,
    normalize,
    reduce_to_device_action,
    reduce_to_device_macro,
    summarize,
)

try:
    from pynput import keyboard as pynput_keyboard
    from pynput import mouse as pynput_mouse
except Exception:  # pragma: no cover - pynput needs an input backend
    pynput_keyboard = None
    pynput_mouse = None

# Raw events this close to a keypad event are the keypad's own HID output
# arriving back at us. Without this the recorder eats its own tail.
SELF_INPUT_WINDOW = 0.15

#: Stops the recording without ending up in it.
STOP_KEY = "esc"

_SPECIAL_NAMES = {
    "alt_l": "alt",
    "alt_r": "ralt",
    "alt_gr": "ralt",
    "ctrl_l": "ctrl",
    "ctrl_r": "rctrl",
    "shift_l": "shift",
    "shift_r": "rshift",
    "cmd": "gui",
    "cmd_l": "gui",
    "cmd_r": "rgui",
    "page_up": "pageup",
    "page_down": "pagedown",
    "caps_lock": "capslock",
    "num_lock": "numlock",
    "scroll_lock": "scrolllock",
    "print_screen": "printscreen",
}


class RecorderError(RuntimeError):
    """Raised when input capture is unavailable."""


class Recorder:
    """Listens globally, then hands back a normalised action list.

    The listener is *not* started implicitly anywhere: recording only happens
    when the user explicitly asks for it, and the UI shows it is happening.
    """

    def __init__(
        self,
        on_event: Callable[[RawEvent], None] | None = None,
        capture_mouse: bool = False,
        min_gap_ms: int = DEFAULT_MIN_GAP_MS,
    ) -> None:
        self._on_event = on_event
        self._capture_mouse = capture_mouse
        self._min_gap_ms = min_gap_ms
        self._events: list[RawEvent] = []
        self._lock = threading.Lock()
        self._keyboard_listener = None
        self._mouse_listener = None
        self._last_device_key_at = 0.0
        self.recording = False

    @staticmethod
    def available() -> tuple[bool, str]:
        if pynput_keyboard is None:
            return False, "pynput is not installed or cannot access an input backend"
        return True, ""

    # ---------------------------------------------------------------- control --

    def start(self) -> None:
        usable, reason = self.available()
        if not usable:
            raise RecorderError(reason)
        if self.recording:
            return

        with self._lock:
            self._events.clear()
        self._keyboard_listener = pynput_keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._keyboard_listener.start()
        if self._capture_mouse and pynput_mouse is not None:
            self._mouse_listener = pynput_mouse.Listener(
                on_click=self._on_click, on_scroll=self._on_scroll
            )
            self._mouse_listener.start()
        self.recording = True

    def stop(self) -> list[RawEvent]:
        self.recording = False
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener is not None:
                listener.stop()
        self._keyboard_listener = None
        self._mouse_listener = None
        with self._lock:
            return list(self._events)

    def note_device_key(self) -> None:
        """Call when the keypad reports a press, so its HID echo is dropped."""
        self._last_device_key_at = time.monotonic()

    # ----------------------------------------------------------------- result --

    def steps(self, events: list[RawEvent] | None = None) -> list[dict[str, Any]]:
        source = events if events is not None else self._events
        return normalize(source, min_gap_ms=self._min_gap_ms)

    @staticmethod
    def summary(steps: list[dict[str, Any]]) -> list[str]:
        return summarize(steps)

    @staticmethod
    def device_action(steps: list[dict[str, Any]]):
        return reduce_to_device_action(steps)

    @staticmethod
    def device_macro(steps: list[dict[str, Any]]):
        """The whole recording as firmware sequence steps, or None."""
        return reduce_to_device_macro(steps)

    # -------------------------------------------------------------- listeners --

    def _record(self, event: RawEvent) -> None:
        if event.at - self._last_device_key_at < SELF_INPUT_WINDOW:
            return
        with self._lock:
            self._events.append(event)
        if self._on_event is not None:
            self._on_event(event)

    def _on_press(self, key) -> None:
        token, char = _describe_key(key)
        if token is None:
            return
        if token == STOP_KEY:
            self.stop()
            return
        self._record(RawEvent(kind=KEY_DOWN, token=token, char=char, at=time.monotonic()))

    def _on_release(self, key) -> None:
        token, char = _describe_key(key)
        if token is None or token == STOP_KEY:
            return
        self._record(RawEvent(kind=KEY_UP, token=token, char=char, at=time.monotonic()))

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        if not pressed:
            return
        name = getattr(button, "name", "left")
        self._record(
            RawEvent(kind=MOUSE_CLICK, token=name, at=time.monotonic(), data=(int(x), int(y)))
        )

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self._record(
            RawEvent(kind=SCROLL, token="scroll", at=time.monotonic(), data=(int(dx), int(dy)))
        )


def _describe_key(key) -> tuple[str | None, str]:
    """pynput key -> ``(macroKey token, printable character)``."""
    if pynput_keyboard is None:
        return None, ""

    if isinstance(key, pynput_keyboard.KeyCode):
        char = key.char or ""
        if char and char.isprintable():
            return char.lower(), char
        return (f"vk{key.vk}", "") if key.vk is not None else (None, "")

    name = getattr(key, "name", None)
    if name is None:
        return None, ""
    token = _SPECIAL_NAMES.get(name, name)
    return token, " " if token == "space" else ""
