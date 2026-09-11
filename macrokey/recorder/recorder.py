"""Captures real input so it can be replayed from a macro key."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from macrokey.config.model import MACRO_MAX_RECORDS

from . import evdev_source
from .evdev_source import MOTION_DEAD_ZONE, MOTION_SLICE_SECONDS
from .events import KEY_DOWN, KEY_UP, MOUSE_CLICK, MOUSE_MOVE, MOUSE_RELEASE, SCROLL, RawEvent
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

log = logging.getLogger(__name__)

# Raw events this close to a keypad event are the keypad's own HID output
# arriving back at us. Without this the recorder eats its own tail.
SELF_INPUT_WINDOW = 0.15

# How long after a captured click this window's own mouse event may arrive and
# still be that same click.
#
# Ordered, not symmetric, and that is the point. Capture sees a click before Qt
# does -- a global hook runs ahead of the delivery to whichever window was hit --
# so the window's event trails the captured one. Allowing the same slack in both
# directions would make the *next* click anywhere else, made shortly after
# clicking this window, look like the same one and disappear.
#
# The negative bound is clock jitter only: both stamps come from the same
# monotonic clock on the same machine, so there is nothing real to absorb.
EDITOR_CLICK_LEAD = 0.30
EDITOR_CLICK_SLACK = 0.05

#: A key that ends the recording instead of being recorded. There is no good
#: default: Esc was one, and it meant a macro could never contain Esc -- which
#: rules out closing a dialog, leaving vim insert mode, and dismissing a
#: completion popup. Callers that have a button to press should pass None and
#: stop the recording that way; the CLI has nowhere to click, so it opts in.
DEFAULT_STOP_KEY = "esc"

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


def _wayland_session() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


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
        anchor_mouse: bool = False,
        preserve_key_timing: bool = False,
        min_gap_ms: int = DEFAULT_MIN_GAP_MS,
        stop_key: str | None = None,
    ) -> None:
        self._on_event = on_event
        self.capture_mouse = capture_mouse
        self.anchor_mouse = anchor_mouse
        self.preserve_key_timing = preserve_key_timing
        self._min_gap_ms = min_gap_ms
        self.stop_key = stop_key
        #: When the editor's own windows were clicked, by the same clock the
        #: captured events carry. Clicks matching these are dropped at the end
        #: of a recording, so operating the editor while hold-to-record runs
        #: does not become a macro step.
        #:
        #: This used to be a screen rectangle, and a rectangle is not a window.
        #: Recording is started from the pad, so the person works in whatever
        #: application the macro is for, and that application is usually
        #: maximised and therefore *over* this one -- every click that happened
        #: to land inside these coordinates was thrown away even though it went
        #: to the window in front. Gating the rectangle on this window being
        #: active was not enough either: the recording that prompted it was made
        #: with the editor sitting behind Excel, where the rectangle should have
        #: been off, and three of its seven clicks still went missing.
        #:
        #: Qt receiving the press *is* the evidence the rectangle was guessing
        #: at, and it needs no coordinates and no platform of its own: the event
        #: reaches a widget here only when the click actually landed on one.
        #: That makes it true for evdev as well, which never had coordinates to
        #: filter by and so could never drop these clicks at all.
        self._editor_click_ats: list[float] = []
        self._events: list[RawEvent] = []
        self._lock = threading.Lock()
        #: Guards the pointer accumulators below. pynput runs the keyboard and
        #: the mouse on *separate* listener threads, and both flush motion now
        #: -- a keystroke ends the slice that preceded it. `pending += dx` is a
        #: read-modify-write, so without this, typing while the pointer moves
        #: could drop a delta or emit a slice half reset. Reentrant because
        #: `_on_move` flushes at a slice boundary while holding it.
        #:
        #: Lock order where both are taken: this one, then `_lock`. Nothing
        #: takes them the other way round -- `_record` touches the event list
        #: and nothing else -- so there is no cycle to deadlock on.
        self._motion_lock = threading.RLock()
        self._keyboard_listener = None
        self._mouse_listener = None
        self._evdev = None
        self.backend = ""
        self._last_device_key_at = 0.0
        self.recording = False
        self._stop_requested = False
        # pynput reports absolute cursor position; deltas are derived here so
        # the rest of the pipeline stays relative, matching evdev.
        self._pynput_last_pos: tuple[int, int] | None = None
        self._pynput_pending_dx = 0
        self._pynput_pending_dy = 0
        self._pynput_motion_started_at: float | None = None

    @staticmethod
    def available() -> tuple[bool, str]:
        """Whether anything here can capture, and the most useful reason if not.

        Reports on the backend that will actually be chosen, not merely on
        whether pynput imports: pynput imports fine under Wayland and then sees
        nothing, which is the failure this exists to stop reporting as success.
        """
        usable, reason = evdev_source.available()
        if usable:
            return True, ""
        if pynput_keyboard is None:
            return False, reason
        if _wayland_session():
            return False, (
                f"{reason}. Without it, capture falls back to X11, which under "
                "Wayland cannot see typing into most windows."
            )
        return True, ""

    # ---------------------------------------------------------------- control --

    def start(self) -> None:
        if self.recording:
            return
        self._stop_requested = False
        with self._lock:
            self._events.clear()
            self._editor_click_ats.clear()

        # Prefer the kernel: it is the only source that sees every window under
        # Wayland. pynput stays as the fallback for boxes without the input
        # group, and on X11 where it works properly.
        if evdev_source.available()[0]:
            self._evdev = evdev_source.EvdevRecorder(
                self._record, capture_mouse=self.capture_mouse
            )
            # Named before it is started, not after: `start` spawns the reading
            # thread, and `_record` asks which backend it is on to decide
            # whether the self-echo blanket applies. Set afterwards, the first
            # events of every recording were judged by the wrong rule.
            self.backend = "evdev"
            self.recording = True
            try:
                self._evdev.start()
            except Exception:
                self.recording = False
                self._evdev = None
                raise
            return

        usable, reason = self.available()
        if not usable:
            raise RecorderError(reason)
        self.backend = "pynput"
        self._pynput_last_pos = None
        if self.capture_mouse and pynput_mouse is not None:
            try:
                position = pynput_mouse.Controller().position
                self._pynput_last_pos = (int(position[0]), int(position[1]))
            except Exception:  # noqa: BLE001 - capture still works after first move
                pass
        self._pynput_pending_dx = self._pynput_pending_dy = 0
        self._pynput_motion_started_at = None
        self.recording = True
        try:
            self._keyboard_listener = pynput_keyboard.Listener(
                on_press=self._on_press, on_release=self._on_release
            )
            self._keyboard_listener.start()
            if self.capture_mouse and pynput_mouse is not None:
                self._mouse_listener = pynput_mouse.Listener(
                    on_click=self._on_click,
                    on_scroll=self._on_scroll,
                    on_move=self._on_move,
                )
                self._mouse_listener.start()
        except Exception:
            self.stop()
            raise

    def stop(self) -> list[RawEvent]:
        self.recording = False
        if self._evdev is not None:
            self._evdev.stop()
            self._evdev = None
            self._drop_editor_clicks()
            with self._lock:
                return list(self._events)
        self._flush_pynput_motion()
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener is not None:
                listener.stop()
        self._keyboard_listener = None
        self._mouse_listener = None
        self._drop_editor_clicks()
        with self._lock:
            return list(self._events)

    def note_device_key(self) -> None:
        """Call when the keypad reports a press, so its HID echo is dropped."""
        self._last_device_key_at = time.monotonic()

    def note_editor_click(self) -> None:
        """Call when one of the editor's own windows receives a mouse press."""
        if not self.recording:
            return
        with self._lock:
            self._editor_click_ats.append(time.monotonic())

    def _drop_editor_clicks(self) -> None:
        """Removes the clicks that were aimed at the editor, not at the macro.

        At the end rather than as they arrive, because the evidence arrives
        second: the capture hook sees a click before Qt delivers it, so at the
        moment there is nothing yet to compare against. A recording is only read
        once it has stopped, so waiting costs nothing.

        A press takes its release with it. Left behind, an orphaned release is
        not merely a stray step -- `normalize` reads a release with no press as
        the tail of a drag that began before capture and skips it, so the pair
        would half-vanish in a way that depends on what came after it.
        """
        with self._lock:
            received = list(self._editor_click_ats)
            if not received:
                return
            events = self._events
            doomed: set[int] = set()
            for index, event in enumerate(events):
                if event.kind != MOUSE_CLICK or index in doomed:
                    continue
                if not any(
                    -EDITOR_CLICK_SLACK <= at - event.at <= EDITOR_CLICK_LEAD
                    for at in received
                ):
                    continue
                doomed.add(index)
                for later in range(index + 1, len(events)):
                    if (
                        events[later].kind == MOUSE_RELEASE
                        and events[later].token == event.token
                    ):
                        doomed.add(later)
                        break
            if not doomed:
                return
            log.info(
                "dropped %d captured event(s): the editor window received those "
                "clicks itself",
                len(doomed),
            )
            self._events = [
                event for index, event in enumerate(events) if index not in doomed
            ]

    # ----------------------------------------------------------------- result --

    def steps(self, events: list[RawEvent] | None = None) -> list[dict[str, Any]]:
        source = events if events is not None else self._events
        return normalize(
            source,
            min_gap_ms=self._min_gap_ms,
            preserve_key_timing=self.preserve_key_timing,
        )

    @staticmethod
    def summary(steps: list[dict[str, Any]]) -> list[str]:
        return summarize(steps)

    @staticmethod
    def device_action(steps: list[dict[str, Any]]):
        return reduce_to_device_action(steps)

    @staticmethod
    def device_macro(
        steps: list[dict[str, Any]], *, anchor_pointer: bool = False,
        max_records: int = MACRO_MAX_RECORDS
    ):
        """The whole recording as firmware sequence steps, or None."""
        return reduce_to_device_macro(
            steps, anchor_pointer=anchor_pointer, max_records=max_records
        )

    # -------------------------------------------------------------- listeners --

    def _record(self, event: RawEvent) -> None:
        if self._stop_requested:
            return
        if self.stop_key is not None and event.token == self.stop_key:
            # evdev callbacks run on their reader thread, so calling stop()
            # here would try to join that same thread. Mark it stopped; the
            # CLI/main thread notices and performs the actual cleanup.
            if event.kind == KEY_DOWN:
                self.recording = False
                self._stop_requested = True
            return
        # Only pynput needs this. It reports keystrokes with no idea which
        # device produced them, so the keypad's own HID output comes back as
        # input and the recorder eats its own tail; blanking a window after a
        # pad event is the only defence available there.
        #
        # evdev knows. It skips the pad's input nodes outright, so nothing of
        # the pad's can arrive -- and leaving the blanket on meant every pad
        # event still threw away 150 ms of what was really being typed, which
        # is the recording losing exactly the keystrokes it was asked for.
        #
        # Discrete events only, and this is the whole of it. A pointer move is
        # not one event: it is 50 ms of travel this recorder added up itself, so
        # blanking it threw away far more than the window it was blanking -- a
        # pad key arriving mid-drag deleted 200 counts of a 312-count gesture.
        # An echo cannot arrive that way; a keystroke or a button can, and those
        # are still covered. The lower bound is the other half of the same bug:
        # a slice that *started* before the pad event has a negative age, which
        # compared as "less than 150 ms" and was dropped as well.
        if (
            self.backend != "evdev"
            and event.kind in (KEY_DOWN, KEY_UP, MOUSE_CLICK, MOUSE_RELEASE, SCROLL)
            and 0.0 <= event.at - self._last_device_key_at < SELF_INPUT_WINDOW
        ):
            # Logged because the other way a click goes missing looks identical
            # from the outside, and telling them apart from a finished recording
            # is guesswork otherwise.
            log.info(
                "dropped a captured %s: %.0f ms after the keypad's own event",
                event.kind,
                (event.at - self._last_device_key_at) * 1000,
            )
            return
        with self._lock:
            self._events.append(event)
        if self._on_event is not None:
            self._on_event(event)

    def _on_press(self, key) -> None:
        token, char = _describe_key(key)
        if token is None:
            return
        # A keystroke is a boundary the pointer's travel belongs *before*. evdev
        # has always flushed here; pynput only did it for the stop key, so a
        # recording of "move there, then type" came back with the move after the
        # typing -- the macro typed into whatever had focus before the pointer
        # was moved, and the pause between the two was measured from the wrong
        # end.
        #
        # Noise-filtered, unlike a click: typing is not aimed at the pointer, so
        # a few counts of drift before it is a hand resting on the mouse rather
        # than a placement. Unfiltered, every character typed with a hand on the
        # mouse became a step of its own.
        self._flush_pynput_motion()
        self._record(RawEvent(kind=KEY_DOWN, token=token, char=char, at=time.monotonic()))

    def _on_release(self, key) -> None:
        token, char = _describe_key(key)
        if token is None or (self.stop_key is not None and token == self.stop_key):
            return
        self._record(RawEvent(kind=KEY_UP, token=token, char=char, at=time.monotonic()))

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        # Not filtered: a click is proof the travel before it was deliberate,
        # however small. Dropping it left the click a few pixels from where it
        # was made, which is the same complaint as a move that stops short.
        self._flush_pynput_motion(filter_noise=False)
        name = getattr(button, "name", "left")
        # Both halves, so normalize can tell a click from the start of a drag.
        self._record(
            RawEvent(
                kind=MOUSE_CLICK if pressed else MOUSE_RELEASE,
                token=name,
                at=time.monotonic(),
                data=(int(x), int(y)),
            )
        )

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self._flush_pynput_motion(filter_noise=False)
        self._record(
            RawEvent(kind=SCROLL, token="scroll", at=time.monotonic(), data=(int(dx), int(dy)))
        )

    def _on_move(self, x: int, y: int) -> None:
        """pynput path: absolute cursor -> accumulated relative move.

        Without this, Include mouse under the X11 fallback only kept clicks and
        the wheel, so a drag-and-drop recording came back as two clicks with the
        pointer never travelling between them.

        The editor's own rectangle used to be subtracted here, and the pointer
        passing over a window is still the pointer travelling: dropping those
        samples deleted whichever part of the gesture crossed it, and the macro
        stopped short by exactly that much. A diagonal drawn across the screen
        crosses a window sitting in the middle of it almost every time, which is
        why the diagonal was the one that never arrived while an edge-hugging
        move was fine. The rectangle is gone entirely now; see
        `_drop_editor_clicks`.
        """
        pos = (int(x), int(y))
        previous, self._pynput_last_pos = self._pynput_last_pos, pos
        if previous is None:
            return
        dx = pos[0] - previous[0]
        dy = pos[1] - previous[1]
        if not dx and not dy:
            return
        now = time.monotonic()
        with self._motion_lock:
            if (
                self._pynput_motion_started_at is not None
                and now - self._pynput_motion_started_at >= MOTION_SLICE_SECONDS
            ):
                self._flush_pynput_motion(filter_noise=False)
            if self._pynput_motion_started_at is None:
                self._pynput_motion_started_at = now
            self._pynput_pending_dx += dx
            self._pynput_pending_dy += dy

    def _flush_pynput_motion(self, *, filter_noise: bool = True) -> None:
        # Held only over the read-and-reset. The event itself goes out after,
        # so the common path takes one lock at a time; `_on_move` is the caller
        # that holds this across `_record`, in the documented order.
        with self._motion_lock:
            dx, dy = self._pynput_pending_dx, self._pynput_pending_dy
            started_at = self._pynput_motion_started_at
            self._pynput_pending_dx = self._pynput_pending_dy = 0
            self._pynput_motion_started_at = None
        if started_at is None:
            return
        if not dx and not dy:
            return
        if filter_noise and abs(dx) < MOTION_DEAD_ZONE and abs(dy) < MOTION_DEAD_ZONE:
            return
        self._record(
            RawEvent(
                kind=MOUSE_MOVE,
                token="move",
                at=started_at,
                data=(dx, dy),
            )
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
