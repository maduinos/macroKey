"""Synthesising keyboard input on the host, via pynput."""

from __future__ import annotations

import time

from ..config import keycodes

try:
    from pynput.keyboard import Controller, Key, KeyCode
except Exception:  # pragma: no cover - pynput needs a display to import
    Controller = None
    Key = None
    KeyCode = None


class KeyboardError(RuntimeError):
    """Raised when host-side key synthesis is unavailable."""


# Our hotkey vocabulary -> pynput's. Only names that differ need an entry;
# single characters pass through untouched.
_PYNPUT_NAMES = {
    "ctrl": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "gui": "cmd",
    "rctrl": "ctrl_r",
    "rshift": "shift_r",
    "ralt": "alt_gr",
    "rgui": "cmd_r",
    "enter": "enter",
    "return": "enter",
    "esc": "esc",
    "escape": "esc",
    "tab": "tab",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "pageup": "page_up",
    "pagedown": "page_down",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "capslock": "caps_lock",
    "printscreen": "print_screen",
    "scrolllock": "scroll_lock",
    "pause": "pause",
    "numlock": "num_lock",
    "menu": "menu",
}
_PYNPUT_NAMES.update({f"f{n}": f"f{n}" for n in range(1, 21)})


class KeyboardBackend:
    def __init__(self) -> None:
        self._controller = None

    def available(self) -> tuple[bool, str]:
        if Controller is None:
            return False, "pynput is not installed or has no input backend here"
        return True, ""

    def require(self) -> None:
        usable, reason = self.available()
        if not usable:
            raise KeyboardError(reason)
        if self._controller is None:
            self._controller = Controller()

    def resolve(self, token: str):
        """Maps one hotkey token to a pynput key object or character."""
        name = _PYNPUT_NAMES.get(token)
        if name is not None:
            key = getattr(Key, name, None)
            if key is not None:
                return key
        if len(token) == 1:
            return token
        raise KeyboardError(f"cannot send key {token!r} on this host")

    def tap_hotkey(self, hotkey: str, hold_ms: int = 10) -> None:
        """Presses a combination such as ``ctrl+shift+v`` and releases it."""
        self.require()
        tokens = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
        if not tokens:
            raise KeyboardError("hotkey is empty")

        modifiers = [token for token in tokens if token in keycodes.MODIFIER_BITS]
        others = [token for token in tokens if token not in keycodes.MODIFIER_BITS]

        pressed = []
        try:
            for token in modifiers:
                key = self.resolve(token)
                self._controller.press(key)
                pressed.append(key)
            for token in others:
                key = self.resolve(token)
                self._controller.press(key)
                time.sleep(hold_ms / 1000)
                self._controller.release(key)
        finally:
            # Releasing in reverse order matters: a stuck modifier is the worst
            # possible failure mode for a macro tool.
            for key in reversed(pressed):
                try:
                    self._controller.release(key)
                except Exception:
                    pass

    def type_text(self, text: str) -> None:
        self.require()
        self._controller.type(text)


_backend: KeyboardBackend | None = None


def get_keyboard_backend() -> KeyboardBackend:
    global _backend
    if _backend is None:
        _backend = KeyboardBackend()
    return _backend
