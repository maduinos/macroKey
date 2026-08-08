"""Wires the pieces together. No UI toolkit is imported from here.

Everything the GUI can do, this class can do headless: connect, sync, run host
actions, drive the LEDs, record. The GUI is a view over this object, which is
what keeps a daemon mode possible.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .actions import HostActionRunner
from .backends import active_window_title
from .config import Profile, Settings, binary, load_profile, save_profile
from .device import ChordEvent, DeviceClient, DeviceError, HostEvent, KeyEvent
from .led import LedService
from .recorder import Recorder

log = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]
EventCallback = Callable[[object], None]

APP_LAYER_POLL_SECONDS = 1.0


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
        self.recorder = Recorder(min_gap_ms=self.settings.recorder_min_gap_ms)

        self._app_layer_rules: dict[str, int] = {}
        self._app_layer_thread: threading.Thread | None = None
        self._app_layer_stop = threading.Event()

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
        self.device.connect(port or self.settings.port)
        if self.settings.led_enabled:
            self.leds = LedService(self.device, status=self.status)
            self.leds.start(listen=self.settings.agentpet_enabled)

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
        elif isinstance(event, ChordEvent):
            self.status(f"Chord {'+'.join(str(k + 1) for k in event.keys)}")

        for callback in list(self._event_callbacks):
            try:
                callback(event)
            except Exception:  # noqa: BLE001
                log.exception("event callback raised")

    # --------------------------------------------------------------- profile --

    def save(self) -> None:
        save_profile(self.profile)
        self.actions.set_profile(self.profile)
        self.status("Profile saved")

    def push_profile(self) -> None:
        """Writes the host profile to the device."""
        blob = binary.encode_profile(self.profile)
        self.device.write_profile(blob)

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

    def start_recording(self) -> None:
        self.recorder.start()
        if self.leds is not None:
            self.leds.show_recording(True)
        self.status("Recording. Press Esc to stop.")

    def stop_recording(self) -> list[dict]:
        events = self.recorder.stop()
        if self.leds is not None:
            self.leds.show_recording(False)
        steps = self.recorder.steps(events)
        self.status(f"Recorded {len(steps)} step(s)")
        return steps

    def assign_recording(
        self, steps: list[dict], layer: int, key: int, gesture: str, name: str = ""
    ) -> str:
        """Stores a recording in a slot, device-side when it fits.

        Returns a short description of where it ended up.
        """
        from .config import Action, HostAction

        device_action = self.recorder.device_action(steps)
        if device_action is not None:
            self.profile.set_action(layer, key, gesture, device_action)
            return f"device action: {device_action.describe()}"

        token = self.profile.next_host_token()
        spec = HostAction(
            type="sequence",
            name=name or f"Recording L{layer}K{key + 1} {gesture}",
            params={"steps": steps},
        )
        self.profile.host_actions[token] = spec
        self.profile.set_action(layer, key, gesture, Action(kind="host", token=token))
        return f"host action #{token} ({len(steps)} steps)"
