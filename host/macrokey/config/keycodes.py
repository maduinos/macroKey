"""Key name <-> byte translation for the Arduino ``Keyboard`` library.

The firmware stores one modifier mask byte plus one keycode byte per ``key``
action. Printable ASCII is passed through as-is; everything else uses the
``KEY_*`` constants from ``Keyboard.h``, which start at 0x80.
"""

from __future__ import annotations

MOD_CTRL = 0x01
MOD_SHIFT = 0x02
MOD_ALT = 0x04
MOD_GUI = 0x08
MOD_RCTRL = 0x10
MOD_RSHIFT = 0x20
MOD_RALT = 0x40
MOD_RGUI = 0x80

MODIFIER_BITS: dict[str, int] = {
    "ctrl": MOD_CTRL,
    "control": MOD_CTRL,
    "shift": MOD_SHIFT,
    "alt": MOD_ALT,
    "option": MOD_ALT,
    "gui": MOD_GUI,
    "win": MOD_GUI,
    "windows": MOD_GUI,
    "cmd": MOD_GUI,
    "super": MOD_GUI,
    "meta": MOD_GUI,
    "rctrl": MOD_RCTRL,
    "rshift": MOD_RSHIFT,
    "ralt": MOD_RALT,
    "altgr": MOD_RALT,
    "rgui": MOD_RGUI,
}

# Canonical spelling used when formatting a mask back into text.
MODIFIER_ORDER: list[tuple[int, str]] = [
    (MOD_CTRL, "ctrl"),
    (MOD_ALT, "alt"),
    (MOD_SHIFT, "shift"),
    (MOD_GUI, "gui"),
    (MOD_RCTRL, "rctrl"),
    (MOD_RALT, "ralt"),
    (MOD_RSHIFT, "rshift"),
    (MOD_RGUI, "rgui"),
]

# Values mirror Keyboard.h. Changing the Arduino core version means checking
# this table against it.
NAMED_KEYS: dict[str, int] = {
    "up": 0xDA,
    "down": 0xD9,
    "left": 0xD8,
    "right": 0xD7,
    "backspace": 0xB2,
    "tab": 0xB3,
    "enter": 0xB0,
    "return": 0xB0,
    "menu": 0xED,
    "esc": 0xB1,
    "escape": 0xB1,
    "insert": 0xD1,
    "delete": 0xD4,
    "pageup": 0xD3,
    "pagedown": 0xD6,
    "home": 0xD2,
    "end": 0xD5,
    "capslock": 0xC1,
    "printscreen": 0xCE,
    "scrolllock": 0xCF,
    "pause": 0xD0,
    "numlock": 0xDB,
    "space": 0x20,
}
NAMED_KEYS.update({f"f{n}": 0xC2 + n - 1 for n in range(1, 13)})
# F13..F24 are the classic macro-pad target: no application binds them, so they
# never collide with a shortcut the user already has.
NAMED_KEYS.update({f"f{n}": 0xF0 + n - 13 for n in range(13, 25)})

_KEY_NAMES: dict[int, str] = {}
for _name, _code in NAMED_KEYS.items():
    _KEY_NAMES.setdefault(_code, _name)


class KeyParseError(ValueError):
    """Raised when a hotkey string cannot be represented on the device."""


def parse_hotkey(text: str) -> tuple[int, int]:
    """``"ctrl+shift+a"`` -> ``(modifier_mask, keycode)``.

    A hotkey may be modifiers only (a sticky/one-shot modifier action), in which
    case the keycode is 0.
    """
    if not text or not text.strip():
        raise KeyParseError("hotkey is empty")

    modifiers = 0
    keycode = 0
    parts = [part.strip().lower() for part in text.split("+")]
    # A trailing "+" means the literal plus key, e.g. "ctrl++".
    parts = [part for part in parts if part] or ["+"]

    for part in parts:
        if part in MODIFIER_BITS:
            modifiers |= MODIFIER_BITS[part]
            continue
        if keycode != 0:
            raise KeyParseError(f"more than one non-modifier key in {text!r}")
        keycode = _keycode_for(part, text)

    return modifiers, keycode


def _keycode_for(part: str, original: str) -> int:
    if part in NAMED_KEYS:
        return NAMED_KEYS[part]
    if len(part) == 1:
        code = ord(part)
        if 0x20 <= code <= 0x7E:
            return code
    raise KeyParseError(f"unknown key {part!r} in {original!r}")


def format_hotkey(modifiers: int, keycode: int) -> str:
    """Inverse of :func:`parse_hotkey`, for display and round-tripping."""
    parts = [name for bit, name in MODIFIER_ORDER if modifiers & bit]
    if keycode:
        parts.append(key_name(keycode))
    return "+".join(parts) if parts else "none"


def key_name(keycode: int) -> str:
    if keycode in _KEY_NAMES:
        return _KEY_NAMES[keycode]
    if 0x21 <= keycode <= 0x7E:
        return chr(keycode)
    return f"0x{keycode:02x}"


# HID consumer page usages. Only reachable when the firmware is built with
# MK_USE_HID_PROJECT=1; otherwise the device reports the action as unsupported.
CONSUMER_USAGES: dict[str, int] = {
    "volume_up": 0x00E9,
    "volume_down": 0x00EA,
    "mute": 0x00E2,
    "play_pause": 0x00CD,
    "stop": 0x00B7,
    "next_track": 0x00B5,
    "prev_track": 0x00B6,
    "brightness_up": 0x006F,
    "brightness_down": 0x0070,
}

MOUSE_BUTTONS: dict[str, int] = {"left": 0x01, "right": 0x02, "middle": 0x04}
