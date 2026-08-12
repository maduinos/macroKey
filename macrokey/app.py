"""Wires the pieces together. No UI toolkit is imported from here.

Everything the GUI can do, this class can do headless: connect, sync, record.
The GUI is a view over this object. After configuration the app can quit -- the
pad runs as a USB HID device on its own.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from .config import Profile, ProfileError, Settings, binary, load_profile, save_profile
from .device import (
    DeviceClient,
    DeviceError,
    KeyEvent,
    RecordRequest,
    candidates,
)
from .recorder import Recorder
from .recorder.normalize import redact_secrets

log = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]
EventCallback = Callable[[object], None]

#: How long the pad acknowledges a write for. Long enough to catch the eye
#: while looking at the keypad, short enough not to sit there.
CONFIRM_FLASH_MS = 600


class MacroKeyApp:
    def __init__(self, status: StatusCallback | None = None) -> None:
        self.settings = Settings.load()
        self.profile: Profile = load_profile()
        self._status_callbacks: list[StatusCallback] = []
        self._event_callbacks: list[EventCallback] = []
        if status is not None:
            self._status_callbacks.append(status)

        self.device = DeviceClient(on_event=self._on_device_event, on_status=self.status)
        self.recorder = Recorder(
            min_gap_ms=self.settings.recorder_min_gap_ms,
            capture_mouse=self.settings.recorder_capture_mouse,
        )
        #: How many steps the last recording lost to the password filter.
        self.last_redacted = 0
        #: Set by whoever wants to drive hold-to-record; None disables it.
        self.session = None

        # Record requests are handled here, one at a time, and never on the
        # thread that delivered them. See `_record_worker`.
        self._record_queue: queue.Queue[tuple[int, str]] = queue.Queue()
        self._record_thread: threading.Thread | None = None

    # ------------------------------------------------------------ observers --

    def on_status(self, callback: StatusCallback) -> None:
        self._status_callbacks.append(callback)

    def on_event(self, callback: EventCallback) -> None:
        self._event_callbacks.append(callback)

    def status(self, message: str) -> None:
        log.info("%s", message)
        for callback in list(self._status_callbacks):
            try:
                callback(message)
            except Exception:  # noqa: BLE001 - a broken view must not stop the app
                log.exception("status callback raised")

    # ------------------------------------------------------------- lifecycle --

    def connect(self, port: str = "") -> None:
        """Opens the keypad, falling back to discovery when a named port is gone.

        The port number changes whenever the board re-enumerates, which happens
        on every firmware upload, so a remembered port goes stale routinely.
        Failing on that instead of looking again would mean the app cannot find
        a device that is plugged in and working.
        """
        wanted = port or self.settings.port
        if wanted and wanted not in {item.device for item in candidates()}:
            self.status(f"{wanted} is gone; looking for the keypad")
            wanted = ""
        self.device.connect(wanted)

    def disconnect(self) -> None:
        self.device.disconnect()

    def close(self) -> None:
        self.disconnect()
        if self.recorder.recording:
            self.recorder.stop()

    # ---------------------------------------------------------------- events --

    def _on_device_event(self, event: object) -> None:
        if isinstance(event, KeyEvent):
            # Tell the recorder so it drops the keypad's own HID echo.
            self.recorder.note_device_key()
        elif isinstance(event, RecordRequest):
            if self.session is None:
                self.status(
                    f"Key {event.key + 1} asked to record, but this app is not "
                    "listening — open the editor (macrokey) to program the pad"
                )
                # Distinct from recording-red: the pad asked, nobody answered.
                try:
                    self.device.set_led_mode(True, timeout_ms=900)
                    self.device.set_all((255, 140, 0), effect="flash", period=900)
                except DeviceError:
                    pass
            else:
                # Not inline. This callback *is* the serial reader thread, and
                # handling a record request means talking to the device: setting
                # the LED, then writing the whole profile. Every one of those
                # waits for a reply only the reader thread can deliver -- so
                # running them here means waiting on ourselves.
                self._queue_record_request(event.key, event.gesture)

        for callback in list(self._event_callbacks):
            try:
                callback(event)
            except Exception:  # noqa: BLE001
                log.exception("event callback raised")

    def request_record(self, key: int, gesture: str = "tap") -> None:
        """Asks the session to start or finish recording into `key`.

        Public because the session's own watchdog needs it: a recording that has
        run too long has to be finished, and that must happen on the worker like
        every other request rather than on whatever thread noticed.
        """
        self._queue_record_request(key, gesture)

    def _queue_record_request(self, key: int, gesture: str = "tap") -> None:
        """Hands a record request to the worker, starting it on first use."""
        if self._record_thread is None or not self._record_thread.is_alive():
            self._record_thread = threading.Thread(
                target=self._record_worker, name="macrokey-record", daemon=True
            )
            self._record_thread.start()
        self._record_queue.put((key, gesture))

    def _record_worker(self) -> None:
        """Runs record requests in order, off the reader thread.

        One thread rather than one per request: start and finish are two halves
        of the same state machine, and handling them concurrently would let a
        finish overtake the start it belongs to.
        """
        while True:
            key, gesture = self._record_queue.get()
            session = self.session
            if session is None:
                continue
            try:
                session.handle_request(key, gesture)
            except Exception:  # noqa: BLE001 - a bad recording must not end the thread
                log.exception("record request for key %d failed", key + 1)
                self.status(f"Key {key + 1}: recording failed, see the log")

    # --------------------------------------------------------------- profile --

    def save(self) -> None:
        save_profile(self.profile)
        self.status("Profile saved")

    def push_profile(self) -> None:
        """Writes the host profile to the device, then says so on the pad."""
        blob = binary.encode_profile(self.profile)
        self.device.write_profile(blob)
        self.confirm_on_device()

    def confirm_on_device(self, color: tuple[int, int, int] = (0, 255, 60)) -> None:
        """Flashes the pixel to acknowledge a write.

        A message in a status bar is in the wrong place: the thing that changed
        is the keypad, and that is where the person is looking after pressing
        Write. Failing to flash is not a failure of the write, so it is swallowed.
        """
        try:
            # A short window, so a crash between here and the release cannot
            # leave the pad stuck on the confirmation colour.
            self.device.set_led_mode(True, timeout_ms=CONFIRM_FLASH_MS)
            self.device.set_all(color, effect="flash", period=CONFIRM_FLASH_MS)
        except DeviceError:
            pass

    def pull_profile(self) -> Profile:
        """Reads the device profile without adopting it."""
        return binary.decode_profile(self.device.read_profile())

    def device_matches_host(self) -> bool:
        """True when device and host hold the same profile bytes."""
        try:
            return self.device.read_profile() == binary.encode_profile(self.profile)
        except (DeviceError, ValueError):
            return False

    # -------------------------------------------------------------- recording --

    def start_recording(self, on_event=None) -> None:
        """`on_event` receives each raw event as it is captured.

        The editor shows them as they arrive: a recording that is capturing
        nothing looks identical to one that is working until it is stopped,
        which on Wayland is a common and confusing way to lose two minutes.
        """
        if self.recorder.recording:
            raise RuntimeError(
                "a recording is already in progress — finish it before starting another"
            )
        self.recorder._on_event = on_event
        self.recorder.start()
        self.status("Recording.")

    def stop_recording(self) -> list[dict]:
        events = self.recorder.stop()
        self.recorder._on_event = None
        steps = self.recorder.steps(events)
        # Before anything can look at it, let alone store it. Capture is
        # global, so a recording running while a sudo prompt was answered
        # holds that password verbatim -- which has already happened once.
        steps, self.last_redacted = redact_secrets(steps)
        if self.last_redacted:
            self.status(
                f"Dropped {self.last_redacted} step(s) that looked like a password"
            )
        self.status(f"Recorded {len(steps)} step(s)")
        return steps

    def _macro_capacity_used(self, *, ignore_slot: int | None = None) -> int:
        from .config.model import macro_records

        total = 0
        for index, existing in enumerate(self.profile.device_macros):
            if ignore_slot is not None and index == ignore_slot:
                continue
            if existing:
                total += macro_records(existing)
        return total

    def _find_macro_slot(
        self, macro: list, *, also_free: int | None = None
    ) -> int | None:
        """Index that can hold `macro`, or None. Does not mutate the profile.

        `also_free` is a slot that will be released when the binding that owns it
        is replaced -- counted as empty for capacity and reuse.
        """
        from .config.model import MACRO_RECORD_CAPACITY, MACRO_SLOTS, macro_records

        macros = self.profile.device_macros
        needed = macro_records(macro)
        used = self._macro_capacity_used(ignore_slot=also_free)
        if used + needed > MACRO_RECORD_CAPACITY:
            return None

        limit = max(len(macros), MACRO_SLOTS)
        for index in range(limit):
            if also_free is not None and index == also_free:
                return index
            if index >= len(macros) or not macros[index]:
                return index
        if len(macros) < MACRO_SLOTS:
            return len(macros)
        return None

    def recording_fits(self, steps: list[dict], key: int, gesture: str) -> bool:
        """True when `assign_recording` would succeed without changing the profile."""
        if self.recorder.device_action(steps) is not None:
            return True
        macro = self.recorder.device_macro(steps)
        if macro is None:
            return False
        previous = self.profile.action(key, gesture)
        also_free = previous.slot if previous.kind == "sequence" else None
        return self._find_macro_slot(macro, also_free=also_free) is not None

    def assign_recording(
        self, steps: list[dict], key: int, gesture: str, name: str = ""
    ) -> str:
        """Stores a recording on the pad. Raises if it will not fit.

        The existing binding is left untouched until a device placement is known,
        so a rejected recording cannot wipe a working key.
        """
        from .config import Action
        from .config.model import MACRO_SLOTS

        device_action = self.recorder.device_action(steps)
        if device_action is not None:
            self.profile.set_action(key, gesture, device_action)
            freed = self.profile.reclaim_storage()
            if freed:
                log.debug("reclaimed %d macro slot(s)", freed)
            return f"on the keypad: {device_action.describe()}"

        macro = self.recorder.device_macro(steps)
        previous = self.profile.action(key, gesture)
        also_free = previous.slot if previous.kind == "sequence" else None
        slot = self._find_macro_slot(macro, also_free=also_free) if macro is not None else None
        if macro is None or slot is None:
            raise ProfileError(
                "recording does not fit on the keypad (too many steps or macros full). "
                "Shorten it, or clear unused keys and try again."
            )

        macros = self.profile.device_macros
        while len(macros) < MACRO_SLOTS:
            macros.append([])
        while len(macros) <= slot:
            macros.append([])
        if also_free is not None and also_free != slot and also_free < len(macros):
            macros[also_free] = []
        macros[slot] = macro
        self.profile.set_action(key, gesture, Action(kind="sequence", slot=slot))
        freed = self.profile.reclaim_storage()
        if freed:
            log.debug("reclaimed %d macro slot(s)", freed)
        return f"on the keypad: {len(macro)} step macro"
