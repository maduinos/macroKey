"""Input capture that works under Wayland, by reading the kernel directly.

pynput has one Linux backend and it speaks X11. Under a Wayland session that
still loads -- XWayland is there -- and it still reports success, but it only
ever sees input delivered to X11 clients. Typing into a native Wayland window is
invisible to it, so a recording comes back empty with nothing to explain why.
That is most windows on a current GNOME desktop.

Reading ``/dev/input/event*`` happens below the display server, so it sees
everything regardless of which compositor is running or which window has focus.
The cost is a permission: the nodes are ``root:input``, so the account has to be
in the ``input`` group. That is a real privilege -- it is every keystroke on the
machine, including passwords typed into other windows -- which is why this only
runs while a recording is explicitly in progress and never at rest.

Keycodes arrive unmapped, which suits a macro pad: a shortcut is a physical key
plus modifiers, and that is exactly what the kernel reports. Layout-dependent
characters are derived only for the printable keys, using the shift state we
tracked ourselves.
"""

from __future__ import annotations

import selectors
import threading
import time
from collections.abc import Callable

from .events import KEY_DOWN, KEY_UP, MOUSE_CLICK, MOUSE_MOVE, MOUSE_RELEASE, SCROLL, RawEvent

try:  # pragma: no cover - import guard, exercised by absence
    import evdev
    from evdev import ecodes
except Exception:  # noqa: BLE001
    evdev = None
    ecodes = None


#: evdev key name -> macroKey token. Only the ones whose names differ; the rest
#: fall through the generic ``KEY_A`` -> ``a`` rule below.
_TOKENS = {
    "LEFTCTRL": "ctrl",
    "RIGHTCTRL": "rctrl",
    "LEFTSHIFT": "shift",
    "RIGHTSHIFT": "rshift",
    "LEFTALT": "alt",
    "RIGHTALT": "ralt",
    "LEFTMETA": "gui",
    "RIGHTMETA": "rgui",
    "ESC": "esc",
    "ENTER": "enter",
    "KPENTER": "enter",
    "SPACE": "space",
    "BACKSPACE": "backspace",
    "TAB": "tab",
    "CAPSLOCK": "capslock",
    "MINUS": "-",
    "EQUAL": "=",
    "LEFTBRACE": "[",
    "RIGHTBRACE": "]",
    "BACKSLASH": "\\",
    "SEMICOLON": ";",
    "APOSTROPHE": "'",
    "GRAVE": "`",
    "COMMA": ",",
    "DOT": ".",
    "SLASH": "/",
    "PAGEUP": "pageup",
    "PAGEDOWN": "pagedown",
    "PRINTSCREEN": "printscreen",
    "SCROLLLOCK": "scrolllock",
    "NUMLOCK": "numlock",
}

#: Unshifted -> shifted, for the printable keys, on a US layout. Only used to
#: fill in RawEvent.char, which feeds text folding; the token is what binds.
_SHIFTED = {
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
    "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    "-": "_", "=": "+", "[": "{", "]": "}", "\\": "|",
    ";": ":", "'": '"', "`": "~", ",": "<", ".": ">", "/": "?",
}

_BUTTONS = {"BTN_LEFT": "left", "BTN_RIGHT": "right", "BTN_MIDDLE": "middle"}

#: Motion this still or stiller is a hand resting on the mouse, not a gesture.
MOTION_DEAD_ZONE = 6
#: A pointer that has not moved for this long has finished its gesture. Also
#: the select timeout, so a resting pointer is flushed without a second timer.
MOTION_REST_SECONDS = 0.12

_MODIFIER_TOKENS = {"shift", "rshift"}


def available() -> tuple[bool, str]:
    """Whether this backend can be used right now, and why not when it cannot."""
    if evdev is None:
        return False, "python-evdev is not installed"
    try:
        paths = evdev.list_devices()
    except Exception as exc:  # noqa: BLE001
        return False, f"cannot list input devices: {exc}"
    if not paths:
        return False, (
            "no readable input devices: /dev/input/event* is root:input, so this "
            "account needs to be in the 'input' group "
            "(sudo usermod -aG input $USER, then log out and back in)"
        )
    return True, ""


def _token_for(code: int) -> str | None:
    """evdev key code -> macroKey token, or None when it is not a key we bind."""
    name = ecodes.KEY.get(code) or ecodes.BTN.get(code)
    # A code with several aliases comes back as a tuple or a list depending on
    # the table; BTN_LEFT is ("BTN_LEFT", "BTN_MOUSE"). Checking only for list
    # meant every mouse button fell through and no click was ever recorded.
    if isinstance(name, (list, tuple)):
        name = name[0]
    if not isinstance(name, str):
        return None
    if name.startswith("KEY_"):
        bare = name[4:]
        if bare in _TOKENS:
            return _TOKENS[bare]
        if len(bare) == 1:
            return bare.lower()
        if bare.startswith("F") and bare[1:].isdigit():
            return bare.lower()
        return bare.lower()
    return None


class EvdevRecorder:
    """Streams key and mouse events from every keyboard and mouse on the box."""

    def __init__(
        self,
        on_event: Callable[[RawEvent], None],
        *,
        capture_mouse: bool = True,
    ) -> None:
        self._on_event = on_event
        self._capture_mouse = capture_mouse
        self._devices: list = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._shift_held = False
        # Pointer motion arrives as a stream of one-pixel deltas -- a mouse
        # reports hundreds of times a second, so a two-second drag is a few
        # thousand events. They are summed here and flushed as one move when
        # something else happens or the pointer comes to rest, which is what
        # turns "the mouse was moved over there" into a single step.
        self._pending_dx = 0
        self._pending_dy = 0
        self._motion_started_at = 0.0
        self._motion_last_at = 0.0

    def start(self) -> None:
        usable, reason = available()
        if not usable:
            raise RuntimeError(reason)

        for path in evdev.list_devices():
            try:
                device = evdev.InputDevice(path)
            except OSError:
                continue
            capabilities = device.capabilities()
            keys = set(capabilities.get(ecodes.EV_KEY, ()))
            # A keyboard has letter keys; a mouse has BTN_LEFT. Grabbing every
            # node instead would include things like power buttons and lid
            # switches, which produce events nobody wants in a macro.
            is_keyboard = ecodes.KEY_A in keys
            is_mouse = ecodes.BTN_LEFT in keys
            if is_keyboard or (self._capture_mouse and is_mouse):
                self._devices.append(device)
            else:
                device.close()

        if not self._devices:
            raise RuntimeError("no keyboard or mouse found among readable input devices")

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="macrokey-evdev", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        for device in self._devices:
            try:
                device.close()
            except OSError:
                pass
        self._devices = []

    # ------------------------------------------------------------------ loop --

    def _run(self) -> None:
        selector = selectors.DefaultSelector()
        for device in self._devices:
            selector.register(device, selectors.EVENT_READ)
        try:
            while not self._stop.is_set():
                ready = selector.select(timeout=MOTION_REST_SECONDS)
                for key, _mask in ready:
                    device = key.fileobj
                    try:
                        for event in device.read():
                            self._handle(event)
                    except OSError:
                        # Unplugged mid-recording; keep the others going.
                        selector.unregister(device)
                # A pointer that stopped moving has finished its gesture, and
                # nothing else may ever arrive to flush it -- a recording that
                # ends on a mouse movement would otherwise lose it entirely.
                if time.monotonic() - self._motion_last_at >= MOTION_REST_SECONDS:
                    self._flush_motion()
        finally:
            self._flush_motion()
            selector.close()

    def _handle(self, event) -> None:
        now = time.monotonic()

        if event.type == ecodes.EV_REL and event.code in (ecodes.REL_X, ecodes.REL_Y):
            if not self._pending_dx and not self._pending_dy:
                self._motion_started_at = now
            if event.code == ecodes.REL_X:
                self._pending_dx += int(event.value)
            else:
                self._pending_dy += int(event.value)
            self._motion_last_at = now
            return

        # Only the event types that become steps end the move that preceded
        # them, so a click lands after the pointer has been put where the click
        # belongs. Flushing on *any* other type would flush on EV_SYN, which the
        # kernel appends to every single mouse report -- the accumulation would
        # never span more than one report and a drag would be thousands of
        # one-pixel steps instead of one move.
        if event.type not in (ecodes.EV_KEY, ecodes.EV_REL):
            return
        self._flush_motion()

        if event.type == ecodes.EV_KEY:
            name = ecodes.BTN.get(event.code)
            if isinstance(name, (list, tuple)):
                name = next((alias for alias in name if alias in _BUTTONS), name[0])
            if isinstance(name, str) and name in _BUTTONS:
                # Both halves. A press and a release with movement between them
                # is a drag, and normalize needs to see the pair to tell that
                # from a click -- folding the release away here is what made a
                # drag replay as two clicks with the pointer jumping between.
                if event.value == 1:
                    self._emit(RawEvent(kind=MOUSE_CLICK, token=_BUTTONS[name], at=now))
                elif event.value == 0:
                    self._emit(RawEvent(kind=MOUSE_RELEASE, token=_BUTTONS[name], at=now))
                return

            token = _token_for(event.code)
            if token is None:
                return
            if token in _MODIFIER_TOKENS:
                self._shift_held = event.value != 0
            if event.value == 1:
                self._emit(RawEvent(kind=KEY_DOWN, token=token, char=self._char(token), at=now))
            elif event.value == 0:
                self._emit(RawEvent(kind=KEY_UP, token=token, char="", at=now))
            # value == 2 is auto-repeat, which is the keyboard talking, not the
            # person; replaying it would turn a held key into dozens of steps.
            return

        if event.type == ecodes.EV_REL and event.code == ecodes.REL_WHEEL and event.value:
            self._emit(RawEvent(kind=SCROLL, token="scroll", at=now, data=(0, int(event.value))))

    def _flush_motion(self) -> None:
        """Emits the accumulated pointer movement as one event, if any.

        Dated at the moment motion *started*, not at the flush: the delay before
        a move is the pause the person took before reaching for something, and
        stamping it at the end would fold the whole gesture into that pause.
        """
        dx, dy = self._pending_dx, self._pending_dy
        if not dx and not dy:
            return
        self._pending_dx = self._pending_dy = 0
        if abs(dx) < MOTION_DEAD_ZONE and abs(dy) < MOTION_DEAD_ZONE:
            # A hand resting on the mouse. Replaying it does nothing useful and
            # it would sit between two keystrokes as a step that reads as noise.
            return
        self._emit(
            RawEvent(kind=MOUSE_MOVE, token="move", at=self._motion_started_at, data=(dx, dy))
        )

    def _char(self, token: str) -> str:
        if len(token) != 1:
            return " " if token == "space" else ""
        if not self._shift_held:
            return token
        return _SHIFTED.get(token, token.upper())

    def _emit(self, event: RawEvent) -> None:
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a bad consumer must not kill capture
            pass
