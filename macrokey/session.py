"""Hold a key to record into it, hold it again to keep it.

The pad reports "key N was held on its own for three seconds" and nothing more.
All the state lives here, because the device and the host disagreeing about
which mode they are in is the failure that would be impossible to explain: the
pad would be lit as if recording while nothing was captured, or the reverse.

The trigger arriving over serial rather than from a keyboard hook is what makes
this safe to leave running. Listening for a hotkey would mean holding the input
devices open all day; listening to the pad costs nothing, and the input devices
are opened only after the pad asks and closed the moment it asks again.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .device import DeviceError

log = logging.getLogger(__name__)

#: While recording. Red, because this is the one signal that must not be missed:
#: everything typed anywhere is being captured.
RECORDING_COLOR = (255, 0, 0)
#: While recording into the double slot. Still unmistakably hot, but a different
#: hue: the pad has one pixel and no screen, so which of a key's two slots is
#: being programmed has nowhere else to be said while it is happening.
RECORDING_DOUBLE_COLOR = (255, 0, 150)
#: Stored, and the pad can replay it on its own.
SAVED_ON_DEVICE_COLOR = (0, 255, 60)
#: Stored, but it needs this computer running to work.
SAVED_ON_HOST_COLOR = (255, 140, 0)
#: Nothing was captured, or it would not fit.
REJECTED_COLOR = (255, 0, 60)

#: The pad holds the recording colour without being spoken to for this long, and
#: it is refreshed well inside that. Long enough that a slow save does not blink.
LED_HOLD_MS = 30000
FLASH_MS = 900

#: How often the watchdog looks in. Also the LED keepalive period.
WATCH_SECONDS = 5.0
#: A recording left running captures everything typed anywhere, and the password
#: filter is a guess rather than a guarantee. The pad is the only way to finish
#: one, so a pad that has been unplugged or forgotten would otherwise leave
#: capture on for the rest of the session.
MAX_RECORDING_SECONDS = 600.0


@dataclass
class RecordOutcome:
    """What happened when a recording was stored, in terms worth showing."""

    key: int
    steps: int
    where: str
    on_device: bool
    gesture: str = "tap"
    dropped_secrets: int = 0
    error: str = ""


class RecordingSession:
    """Turns record requests from the pad into stored macros.

    One method for the app to call and one callback out. Everything else --
    which key, whether a recording is running, what the LED should say -- is
    decided here so there is exactly one place that knows.
    """

    def __init__(self, app, on_change: Callable[[], None] | None = None) -> None:
        self.app = app
        self._on_change = on_change or (lambda: None)
        self.active_key: int | None = None
        #: Which slot the running recording is for. Decided when it starts, not
        #: when it finishes: the finish only has to say "this key again", so
        #: ending a tap recording with a tap-tap-hold must not silently move it.
        self.active_gesture: str = "tap"
        self.last_outcome: RecordOutcome | None = None
        #: The last recording's normalised steps, kept so a window can show what
        #: was caught. Authoring happens with no screen in front of you.
        self.last_steps: list[dict] = []
        self._started_at = 0.0
        self._watch_stop = threading.Event()

    @property
    def recording(self) -> bool:
        return self.active_key is not None

    # ------------------------------------------------------------- entry point --

    def handle_request(self, key: int, gesture: str = "tap") -> None:
        """A key was held alone. Start recording into it, or finish."""
        if self.active_key is None:
            self._start(key, gesture)
        elif key == self.active_key:
            self._finish()
        else:
            # A different key while one is already recording. Finishing into the
            # key that was actually held would silently bind the wrong slot, so
            # the recording is kept for the key it started on and the second
            # request is ignored with a visible complaint.
            self.app.status(
                f"Already recording into key {self.active_key + 1}. "
                f"Hold key {self.active_key + 1} again to finish."
            )
            self._flash(REJECTED_COLOR)

    def abort(self, reason: str = "Recording cancelled") -> None:
        """Drops a recording in progress without storing it."""
        if self.active_key is None:
            return
        self.active_key = None
        self._watch_stop.set()
        self.app.stop_recording()
        self._release_led()
        self.app.status(reason)
        self._on_change()

    # ---------------------------------------------------------------- internals --

    def _start(self, key: int, gesture: str = "tap") -> None:
        # Before capture, not after: everything the recorder sees from here is
        # measured from the corner, which is what lets the macro be replayed
        # back onto the same pixels. Done by the pad, because it is a real USB
        # mouse -- under Wayland nothing outside the compositor may move the
        # cursor, so the host cannot do this itself.
        #
        # Failure is not fatal. A keyboard-only recording does not care where
        # the pointer is, and refusing to record at all because the cursor
        # could not be parked would be worse than a mouse macro that needs
        # doing again.
        if self.app.recorder.capture_mouse:
            try:
                self.app.device.home_pointer()
            except DeviceError:
                log.debug("could not park the pointer before recording", exc_info=True)

        try:
            self.app.start_recording()
        except Exception as exc:  # noqa: BLE001 - capture backends fail environmentally
            self.app.status(f"Cannot record: {exc}")
            self._flash(REJECTED_COLOR)
            return
        self.active_key = key
        self.active_gesture = gesture
        self._started_at = time.monotonic()
        self._show_recording()
        self._start_watchdog()
        self.app.status(
            f"Recording into key {key + 1} ({gesture}). Hold it again to finish."
        )
        self._on_change()

    # ---------------------------------------------------------------- watchdog --

    def _start_watchdog(self) -> None:
        """Keeps the pixel lit, and ends a recording nothing else can end.

        Its own thread rather than a Qt timer: every call in here blocks on the
        serial link, and on the main thread a pad that stopped answering froze
        the window for the full two second timeout, twice, every five seconds.
        It also means this works with no window at all.
        """
        self._watch_stop.clear()
        threading.Thread(target=self._watch, name="macrokey-recwatch", daemon=True).start()

    def _watch(self) -> None:
        while not self._watch_stop.wait(WATCH_SECONDS):
            key = self.active_key
            if key is None:
                return

            # The pad is the only way to finish a recording. If it is gone,
            # nothing will ever arrive to stop this and capture stays on.
            if not self.app.device.connected:
                self.abort("Keypad disconnected, so the recording was dropped")
                return

            if time.monotonic() - self._started_at >= MAX_RECORDING_SECONDS:
                minutes = int(MAX_RECORDING_SECONDS // 60)
                self.app.status(f"Recording ran for {minutes} minutes; storing it now")
                # Through the record worker, not inline: finishing writes the
                # whole profile, and that belongs on the one thread that owns
                # starting and finishing so the two cannot interleave.
                self.app.request_record(key, self.active_gesture)
                return

            self._show_recording()

    def _finish(self) -> None:
        key = self.active_key
        assert key is not None
        self.active_key = None
        self._watch_stop.set()
        steps = self.app.stop_recording()

        # Every step, written out, every time. A recording is authored blind --
        # there is no screen on the pad and the window need not even be open --
        # so the only way to see that what was captured is not what was done is
        # to be told. "It moved on its own" is what this is for: the answer is
        # in here, and without it the only move is to guess.
        self.last_steps = steps
        for line in self.app.recorder.summary(steps):
            log.info("  captured: %s", line)

        if not steps:
            self.last_outcome = RecordOutcome(
                key, 0, "", False, gesture=self.active_gesture, error="nothing was captured"
            )
            self.app.status(
                f"Nothing was captured, so key {key + 1} ({self.active_gesture}) is unchanged"
            )
            self._flash(REJECTED_COLOR)
            self._on_change()
            return

        try:
            where = self.app.assign_recording(steps, key, self.active_gesture)
        except Exception as exc:  # noqa: BLE001 - a full profile must not crash the pad
            self.last_outcome = RecordOutcome(
                key, len(steps), "", False, gesture=self.active_gesture, error=str(exc)
            )
            self.app.status(f"Could not store the recording: {exc}")
            self._flash(REJECTED_COLOR)
            self._on_change()
            return

        on_device = "keypad" in where
        self.last_outcome = RecordOutcome(
            key=key,
            steps=len(steps),
            where=where,
            on_device=on_device,
            gesture=self.active_gesture,
            dropped_secrets=getattr(self.app, "last_redacted", 0),
        )

        try:
            self.app.save()
            self.app.push_profile()
        except (DeviceError, ValueError, OSError) as exc:
            self.last_outcome.error = str(exc)
            self.app.status(f"Recorded, but could not write it to the keypad: {exc}")
            self._flash(REJECTED_COLOR)
            self._on_change()
            return

        self.app.status(f"Key {key + 1} ({self.active_gesture}): {where}")
        # The captured steps were logged above; this is what the pad will
        # actually do with them, which is not the same list. A macro that
        # touches the pointer gains a step that sends it to the corner first.
        if on_device:
            for line in self._stored_steps(key):
                log.info("  will run: %s", line)
        # push_profile already flashes its own acknowledgement; this replaces it
        # with one that says *where* the macro ended up, which is the part that
        # decides whether the pad works with this app closed.
        self._flash(SAVED_ON_DEVICE_COLOR if on_device else SAVED_ON_HOST_COLOR)
        self._on_change()

    def _stored_steps(self, key: int) -> list[str]:
        """What the pad will actually do, read back out of the profile.

        Not the captured steps: a macro that touches the pointer gains a step
        that sends it to the corner first, long text becomes one typed run, and
        a long move becomes several. Reporting the capture as though it were the
        macro described something the pad was not going to do.
        """
        profile = getattr(self.app, "profile", None)
        if profile is None:
            return []
        action = profile.action(key, self.active_gesture)
        if action.kind != "sequence" or action.slot >= len(profile.device_macros):
            return []
        return [step.describe() for step in profile.device_macros[action.slot]]

    # ---------------------------------------------------------------------- LED --

    def _show_recording(self) -> None:
        try:
            color = (
                RECORDING_DOUBLE_COLOR if self.active_gesture == "double" else RECORDING_COLOR
            )
            self.app.device.set_led_mode(True, timeout_ms=LED_HOLD_MS)
            self.app.device.set_all(color, effect="pulse", period=700)
        except DeviceError:
            log.debug("could not show the recording colour", exc_info=True)

    def _flash(self, color: tuple[int, int, int]) -> None:
        try:
            self.app.device.set_led_mode(True, timeout_ms=FLASH_MS)
            self.app.device.set_all(color, effect="flash", period=FLASH_MS)
        except DeviceError:
            log.debug("could not flash the result colour", exc_info=True)

    def _release_led(self) -> None:
        try:
            self.app.device.set_led_mode(False)
        except DeviceError:
            log.debug("could not hand the pixel back", exc_info=True)
