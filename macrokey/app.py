"""Wires the pieces together. No UI toolkit is imported from here.

Everything the GUI can do, this class can do headless: connect, sync, run host
actions, drive the LEDs, record. The GUI is a view over this object, which is
what keeps a daemon mode possible.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from .actions import HostActionRunner
from .backends import active_window_title
from .config import Profile, Settings, binary, load_profile, save_profile
from .device import (
    DeviceClient,
    DeviceError,
    HostEvent,
    KeyEvent,
    RecordRequest,
    candidates,
)
from .led import LedService
from .recorder import Recorder
from .recorder.normalize import redact_secrets

log = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]
EventCallback = Callable[[object], None]

APP_LAYER_POLL_SECONDS = 1.0

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
        self.actions = HostActionRunner(self.profile, status=self.status, device=self.device)
        self.leds: LedService | None = None
        self.recorder = Recorder(
            min_gap_ms=self.settings.recorder_min_gap_ms,
            capture_mouse=self.settings.recorder_capture_mouse,
        )
        #: How many steps the last recording lost to the password filter.
        self.last_redacted = 0
        #: Set by whoever wants to drive hold-to-record; None disables it.
        self.session = None

        self._app_layer_rules: dict[str, int] = {}
        self._app_layer_thread: threading.Thread | None = None
        self._app_layer_stop = threading.Event()

        # Record requests are handled here, one at a time, and never on the
        # thread that delivered them. See `_record_worker`.
        self._record_queue: queue.Queue[int] = queue.Queue()
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
        # The LED service is off unless something asks for it. It holds the pixel
        # in host mode and refreshes it every second, which fights the recording
        # colour and leaves the pad lit by this app rather than by its own
        # profile -- and its reason for existing, feeding AgentPet state in, is
        # not something this app does any more.
        if self.settings.led_enabled and self.settings.agentpet_enabled:
            self.leds = LedService(self.device, status=self.status)
            self.leds.start(listen=True)

    def disconnect(self) -> None:
        self.stop_app_layers()
        if self.leds is not None:
            self.leds.stop()
            self.leds = None
        self.device.disconnect()

    def close(self) -> None:
        self.disconnect()
        if self.recorder.recording:
            self.recorder.stop()

    # ---------------------------------------------------------------- events --

    def _on_device_event(self, event: object) -> None:
        if isinstance(event, HostEvent):
            # Runs on its own thread: this callback is the serial reader.
            self.actions.trigger(event.token)
        elif isinstance(event, KeyEvent):
            # Tell the recorder so it drops the keypad's own HID echo.
            self.recorder.note_device_key()
        elif isinstance(event, RecordRequest):
            if self.session is None:
                self.status(f"Key {event.key + 1} asked to record, but nothing is listening")
            else:
                # Not inline. This callback *is* the serial reader thread, and
                # handling a record request means talking to the device: setting
                # the LED, then writing the whole profile. Every one of those
                # waits for a reply only the reader thread can deliver -- so
                # running them here means waiting on ourselves. It never hung
                # outright, which is what made it hard to see: each call just
                # timed out after two seconds, so recording started with no red
                # pixel and finishing ground through twenty timeouts before
                # failing to save.
                self._queue_record_request(event.key)

        for callback in list(self._event_callbacks):
            try:
                callback(event)
            except Exception:  # noqa: BLE001
                log.exception("event callback raised")

    def request_record(self, key: int) -> None:
        """Asks the session to start or finish recording into `key`.

        Public because the session's own watchdog needs it: a recording that has
        run too long has to be finished, and that must happen on the worker like
        every other request rather than on whatever thread noticed.
        """
        self._queue_record_request(key)

    def _queue_record_request(self, key: int) -> None:
        """Hands a record request to the worker, starting it on first use."""
        if self._record_thread is None or not self._record_thread.is_alive():
            self._record_thread = threading.Thread(
                target=self._record_worker, name="macrokey-record", daemon=True
            )
            self._record_thread.start()
        self._record_queue.put(key)

    def _record_worker(self) -> None:
        """Runs record requests in order, off the reader thread.

        One thread rather than one per request: start and finish are two halves
        of the same state machine, and handling them concurrently would let a
        finish overtake the start it belongs to.
        """
        while True:
            key = self._record_queue.get()
            session = self.session
            if session is None:
                continue
            try:
                session.handle_request(key)
            except Exception:  # noqa: BLE001 - a bad recording must not end the thread
                log.exception("record request for key %d failed", key + 1)
                self.status(f"Key {key + 1}: recording failed, see the log")

    # --------------------------------------------------------------- profile --

    def save(self) -> None:
        save_profile(self.profile)
        self.actions.set_profile(self.profile)
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

    # ------------------------------------------------------- app-aware layers --

    def set_app_layer_rules(self, rules: dict[str, int]) -> None:
        """``{window title substring (lowercase): layer}``."""
        self._app_layer_rules = {key.lower(): value for key, value in rules.items()}

    def start_app_layers(self) -> None:
        if not self._app_layer_rules or self._app_layer_thread is not None:
            return
        self._app_layer_stop.clear()
        self._app_layer_thread = threading.Thread(
            target=self._app_layer_loop, name="macrokey-applayer", daemon=True
        )
        self._app_layer_thread.start()

    def stop_app_layers(self) -> None:
        self._app_layer_stop.set()
        thread, self._app_layer_thread = self._app_layer_thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def _app_layer_loop(self) -> None:
        current: int | None = None
        while not self._app_layer_stop.is_set():
            title = (active_window_title() or "").lower()
            target = next(
                (layer for needle, layer in self._app_layer_rules.items() if needle in title),
                self.profile.base_layer,
            )
            if target != current:
                try:
                    self.device.set_layer(target)
                    current = target
                except DeviceError:
                    # The link went away; the reconnect path will re-apply this.
                    current = None
            self._app_layer_stop.wait(APP_LAYER_POLL_SECONDS)

    # -------------------------------------------------------------- recording --

    def start_recording(self, on_event=None) -> None:
        """`on_event` receives each raw event as it is captured.

        The editor shows them as they arrive: a recording that is capturing
        nothing looks identical to one that is working until it is stopped,
        which on Wayland is a common and confusing way to lose two minutes.
        """
        self.recorder._on_event = on_event
        self.recorder.start()
        if self.leds is not None:
            self.leds.show_recording(True)
        self.status("Recording.")

    def stop_recording(self) -> list[dict]:
        events = self.recorder.stop()
        self.recorder._on_event = None
        if self.leds is not None:
            self.leds.show_recording(False)
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

    def _claim_macro_slot(self, macro: list) -> int | None:
        """Finds room for a device macro, or None when the profile is full.

        Storage is shared across all sixteen slots, so a slot being free is not
        enough -- the records have to fit the remaining bytes too. Returning None
        rather than raising lets the caller fall back to a host action, which is
        slower but unbounded.
        """
        from .config.model import MACRO_RECORD_CAPACITY, MACRO_SLOTS, macro_records

        macros = self.profile.device_macros
        while len(macros) < MACRO_SLOTS:
            macros.append([])

        # Records, not actions: a text run is a header plus a record per three
        # characters, and it is records the region runs out of.
        used = sum(macro_records(existing) for existing in macros)
        if used + macro_records(macro) > MACRO_RECORD_CAPACITY:
            return None
        for index, existing in enumerate(macros):
            if not existing:
                macros[index] = macro
                return index
        return None

    def assign_recording(
        self, steps: list[dict], layer: int, key: int, gesture: str, name: str = ""
    ) -> str:
        """Stores a recording in a slot, device-side when it fits.

        Returns a short description of where it ended up.
        """
        from .config import Action, HostAction

        # Clear the binding first, then sweep. Re-recording into a key is the
        # ordinary case, and until this ran the macro being replaced kept its
        # slot for good: sixteen corrections to one key filled all sixteen slots
        # with steps nothing pointed at, after which every recording fell back
        # to a host action and the pad stopped working with the app closed.
        # Clearing before claiming is what lets the new recording reuse the
        # storage the old one was holding.
        self.profile.set_action(layer, key, gesture, Action())
        freed_slots, freed_tokens = self.profile.reclaim_storage()
        if freed_slots or freed_tokens:
            log.debug("reclaimed %d macro slot(s), %d host action(s)", freed_slots, freed_tokens)

        device_action = self.recorder.device_action(steps)
        if device_action is not None:
            self.profile.set_action(layer, key, gesture, device_action)
            return f"on the keypad: {device_action.describe()}"

        # A sequence the firmware can replay itself keeps the pad working with
        # nothing installed, which is the whole point of the device-first split.
        # This is tried before falling back to the host because the firmware has
        # always had the sequence player -- nothing ever filled its slots.
        macro = self.recorder.device_macro(steps)
        if macro is not None:
            slot = self._claim_macro_slot(macro)
            if slot is not None:
                self.profile.set_action(layer, key, gesture, Action(kind="sequence", slot=slot))
                return f"on the keypad: {len(macro)} step macro"

        token = self.profile.next_host_token()
        spec = HostAction(
            type="sequence",
            name=name or f"Recording L{layer}K{key + 1} {gesture}",
            params={"steps": steps},
        )
        self.profile.host_actions[token] = spec
        self.profile.set_action(layer, key, gesture, Action(kind="host", token=token))
        return f"host action #{token} ({len(steps)} steps)"
