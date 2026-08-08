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
from collections.abc import Callable
from dataclasses import dataclass

from .device import DeviceError

log = logging.getLogger(__name__)

#: While recording. Red, because this is the one signal that must not be missed:
#: everything typed anywhere is being captured.
RECORDING_COLOR = (255, 0, 0)
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


@dataclass
class RecordOutcome:
    """What happened when a recording was stored, in terms worth showing."""

    key: int
    steps: int
    where: str
    on_device: bool
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
        self.last_outcome: RecordOutcome | None = None

    @property
    def recording(self) -> bool:
        return self.active_key is not None

    # ------------------------------------------------------------- entry point --

    def handle_request(self, key: int) -> None:
        """A key was held alone. Start recording into it, or finish."""
        if self.active_key is None:
            self._start(key)
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

    def abort(self) -> None:
        """Drops a recording in progress without storing it."""
        if self.active_key is None:
            return
        self.app.stop_recording()
        self.active_key = None
        self._release_led()
        self.app.status("Recording cancelled")
        self._on_change()

    # ---------------------------------------------------------------- internals --

    def _start(self, key: int) -> None:
        try:
            self.app.start_recording()
        except Exception as exc:  # noqa: BLE001 - capture backends fail environmentally
            self.app.status(f"Cannot record: {exc}")
            self._flash(REJECTED_COLOR)
            return
        self.active_key = key
        self._show_recording()
        self.app.status(f"Recording into key {key + 1}. Hold it again to finish.")
        self._on_change()

    def _finish(self) -> None:
        key = self.active_key
        assert key is not None
        self.active_key = None
        steps = self.app.stop_recording()

        if not steps:
            self.last_outcome = RecordOutcome(key, 0, "", False, error="nothing was captured")
            self.app.status(f"Nothing was captured, so key {key + 1} is unchanged")
            self._flash(REJECTED_COLOR)
            self._on_change()
            return

        try:
            where = self.app.assign_recording(steps, 0, key, "tap")
        except Exception as exc:  # noqa: BLE001 - a full profile must not crash the pad
            self.last_outcome = RecordOutcome(key, len(steps), "", False, error=str(exc))
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

        self.app.status(f"Key {key + 1}: {where}")
        # push_profile already flashes its own acknowledgement; this replaces it
        # with one that says *where* the macro ended up, which is the part that
        # decides whether the pad works with this app closed.
        self._flash(SAVED_ON_DEVICE_COLOR if on_device else SAVED_ON_HOST_COLOR)
        self._on_change()

    # ---------------------------------------------------------------------- LED --

    def _show_recording(self) -> None:
        try:
            self.app.device.set_led_mode(True, timeout_ms=LED_HOLD_MS)
            self.app.device.set_all(RECORDING_COLOR, effect="pulse", period=700)
        except DeviceError:
            log.debug("could not show the recording colour", exc_info=True)

    def refresh_led(self) -> None:
        """Keeps the recording colour alive. Cheap; call it every few seconds."""
        if self.active_key is None:
            return
        self._show_recording()

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
