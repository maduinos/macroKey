"""Keeps the strip showing the current state and the watchdog fed."""

from __future__ import annotations

import logging
import threading

from ..device import DeviceClient, DeviceError
from .palette import DEFAULT_SCENE, RECORDING_SCENE, LedScene, scene_for
from .source import ActivityEvent, ActivityEventServer

log = logging.getLogger(__name__)

#: The firmware drops host control after 3 s of silence, so refresh well inside
#: that. A repeated identical frame is cheap: the device skips unchanged output.
KEEPALIVE_SECONDS = 1.0


class LedService:
    def __init__(self, device: DeviceClient, status=None) -> None:
        self._device = device
        self._status = status or (lambda message: None)
        self._server: ActivityEventServer | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._scene: LedScene = DEFAULT_SCENE
        self._override: LedScene | None = None
        self._last_sent: tuple | None = None
        self.endpoint = ""

    # ---------------------------------------------------------------- control --

    def start(self, listen: bool = True) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        if listen:
            self._server = ActivityEventServer(self._on_activity)
            try:
                self.endpoint = self._server.start()
                self._status(f"Listening for state events on {self.endpoint}")
            except OSError as exc:
                self._server = None
                self._status(f"Could not open the state socket: {exc}")

        self._thread = threading.Thread(target=self._loop, name="macrokey-led", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        if self._server is not None:
            self._server.stop()
            self._server = None
        try:
            # Hand the strip back so it shows the profile scene, rather than
            # leaving it on whatever we last pushed.
            self._device.set_led_mode(False)
        except DeviceError:
            pass

    # ------------------------------------------------------------------ input --

    def set_scene(self, scene: LedScene) -> None:
        with self._lock:
            self._scene = scene
        self._wake.set()

    def set_override(self, scene: LedScene | None) -> None:
        """Takes priority over incoming state, e.g. while recording."""
        with self._lock:
            self._override = scene
        self._wake.set()

    def show_recording(self, active: bool) -> None:
        self.set_override(RECORDING_SCENE if active else None)

    def _on_activity(self, event: ActivityEvent) -> None:
        self.set_scene(scene_for(event.state, event.severity, event.progress))

    # ------------------------------------------------------------------- loop --

    def _loop(self) -> None:
        try:
            self._device.set_led_mode(True)
        except DeviceError as exc:
            self._status(f"Device refused host LED control: {exc}")
            return

        while not self._stop.is_set():
            with self._lock:
                scene = self._override or self._scene
            try:
                self._send(scene)
            except DeviceError as exc:
                self._status(f"LED update failed: {exc}")
                return
            self._wake.wait(KEEPALIVE_SECONDS)
            self._wake.clear()

    def _send(self, scene: LedScene) -> None:
        key = (scene.color, scene.effect, scene.period_ms, scene.bar_percent)
        if key == self._last_sent:
            # Nothing visual changed, but the watchdog still needs a heartbeat.
            self._device.ping()
            return
        if scene.bar_percent is not None:
            self._device.set_bar(scene.bar_percent, scene.color)
        else:
            self._device.set_all(scene.color, scene.effect, scene.period_ms)
        self._last_sent = key
