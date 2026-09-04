"""Reading a shortcut from the real keyboard, the way desktop settings do.

A shortcut field that listens through Qt only hears what the desktop chose to
deliver to it. That is most keys and not the interesting ones: the compositor
keeps `super`, `alt+tab`, `print`, and every media key for itself, and under
Wayland it may keep more than that. GNOME's own "Set Shortcut" dialog gets
around this by taking the keyboard for the duration -- press whatever you like,
it lands in the field and nowhere else -- and this is that, built on the input
nodes the recorder already reads.

Two things make it different from `recorder.evdev_source`, which is why it is
its own module rather than a flag on that one:

* it takes the devices *exclusively* (``EVIOCGRAB``), so the combination being
  bound does not also fire whatever it is currently bound to. A recorder must
  never do that -- what it is recording has to actually happen.
* it wants one key press, not a stream, so it ends itself on the first key that
  is not a modifier.

The exclusive grab is the part to be careful with: while it is held, the
keyboard talks to this process and to nothing else. Every path out of it is
therefore short -- any non-modifier key ends it, closing the dialog ends it, an
idle timeout ends it, and the file descriptors are closed on the way out, which
releases the grab even if all of that fails.
"""

from __future__ import annotations

import logging
import selectors
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from ..config import keycodes
from ..i18n import tr
from ..recorder import evdev_source

log = logging.getLogger(__name__)

#: How long the keyboard may be held with nothing pressed. Purely a safety
#: catch: the grab normally ends on the first key, and the person is looking at
#: a dialog that says it is listening. Long enough to think, short enough that a
#: forgotten window does not own the keyboard for the rest of the session.
IDLE_TIMEOUT_MS = 30_000

#: The tokens that are a shortcut's modifiers rather than its key. Taken from
#: the same table that formats a stored hotkey, so what is captured here reads
#: back identically once it has been through the device.
MODIFIER_TOKENS: frozenset[str] = frozenset(name for _bit, name in keycodes.MODIFIER_ORDER)


def canonical(held, key: str = "") -> str:
    """``{"shift", "ctrl"}, "f13"`` -> ``"ctrl+shift+f13"``.

    Ordered by `keycodes.MODIFIER_ORDER` rather than by when the keys went down,
    so pressing the same combination twice always spells it the same way, and
    spells it the way `format_hotkey` will after a round trip through the pad.
    """
    parts = [name for _bit, name in keycodes.MODIFIER_ORDER if name in held]
    if key:
        parts.append(key)
    return "+".join(parts)


class KeyGrab(QObject):
    """One combination, read from the kernel, delivered on the Qt thread.

    Live for the length of one "press keys" -- `start`, one `captured`, done.
    The reading happens on a thread of its own because the device read blocks;
    it only ever emits a private signal, so every decision and every widget
    touch stays where Qt expects it.
    """

    #: The combination that was pressed, spelled in this project's vocabulary.
    captured = Signal(str)
    #: The modifiers currently held, e.g. ``"ctrl+shift+"``, so the field can
    #: show the shortcut building up. Empty when nothing is held.
    progress = Signal(str)
    #: Listening has stopped. Carries why, or "" when a combination arrived.
    finished = Signal(str)

    #: Reader thread -> Qt thread. Nothing outside this class connects to it.
    _raw = Signal(int, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.active = False
        #: Whether the keyboard is ours alone. False means the keys reach the
        #: desktop as well, which the dialog says out loud -- a combination the
        #: desktop owns will act on it while it is being bound.
        self.exclusive = False
        self._devices: list = []
        self._grabbed: list = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._held: set[str] = set()
        self._raw.connect(self._on_raw)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(IDLE_TIMEOUT_MS)
        self._idle.timeout.connect(self._timed_out)

    # ---------------------------------------------------------------- control --

    def start(self) -> tuple[bool, str]:
        """Begins listening. Returns whether it could, and why not if it could not."""
        if self.active:
            return True, ""

        usable, reason = evdev_source.available()
        if not usable:
            return False, reason
        try:
            devices = self._open_devices()
        except OSError as exc:  # pragma: no cover - device churn mid-enumeration
            return False, str(exc)
        if not devices:
            return False, tr("no keyboard among the readable input devices")

        self._devices = devices
        self._held.clear()
        self._take_exclusive()
        self._stop.clear()
        self.active = True
        self._thread = threading.Thread(
            target=self._run, name="macrokey-keygrab", daemon=True
        )
        self._thread.start()
        self._idle.start()
        return True, ""

    def stop(self, reason: str = "") -> None:
        """Gives the keyboard back. Safe to call when it was never taken."""
        if not self.active and not self._devices and self._thread is None:
            return
        self.active = False
        self._idle.stop()
        self._stop.set()

        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

        for device in self._grabbed:
            try:
                device.ungrab()
            except OSError:
                # Already gone -- unplugged, or the fd is being closed below,
                # which releases the grab anyway.
                pass
        self._grabbed = []
        for device in self._devices:
            try:
                device.close()
            except OSError:
                pass
        self._devices = []
        self._held.clear()
        self.exclusive = False
        self.finished.emit(reason)

    # ----------------------------------------------------------------- devices --

    def _open_devices(self) -> list:
        """Every keyboard that is not the keypad itself.

        The pad is a USB keyboard, so it turns up here like any other; reading
        it would let a pad key bind itself. `KEY_A` is the test for "keyboard"
        because grabbing every node would include lid switches and power
        buttons, which are not keys anyone is trying to press.
        """
        evdev, ecodes = evdev_source.evdev, evdev_source.ecodes
        devices = []
        for path in evdev.list_devices():
            try:
                device = evdev.InputDevice(path)
            except OSError:
                continue
            if (device.info.vendor, device.info.product) in evdev_source.KEYPAD_USB_IDS:
                device.close()
                continue
            try:
                keys = set(device.capabilities().get(ecodes.EV_KEY, ()))
            except OSError:
                # Unplugged between being listed and being asked. Close it here
                # rather than letting the descriptor leak out of a raise.
                device.close()
                continue
            if ecodes.KEY_A in keys:
                devices.append(device)
            else:
                device.close()
        return devices

    def _take_exclusive(self) -> None:
        """Takes the keyboard away from everything else, when that is safe.

        Not when a key is already down: the desktop has seen that press, and
        taking its release away would leave the key held as far as the desktop
        is concerned -- a stuck ctrl is a much worse outcome than a shortcut
        that also fires once while it is being bound.
        """
        self.exclusive = False
        for device in self._devices:
            try:
                if device.active_keys():
                    log.debug("not grabbing %s: a key is already down", device.path)
                    return
            except OSError:
                return

        for device in self._devices:
            try:
                device.grab()
            except OSError as exc:
                # Something else holds it (another grabbing client). The others
                # are still worth having: the key will be seen, it just also
                # reaches the desktop.
                log.debug("could not grab %s: %s", device.path, exc)
                continue
            self._grabbed.append(device)
        self.exclusive = bool(self._grabbed)

    # -------------------------------------------------------------------- loop --

    def _run(self) -> None:
        selector = selectors.DefaultSelector()
        for device in self._devices:
            selector.register(device, selectors.EVENT_READ)
        try:
            while not self._stop.is_set():
                # A timeout rather than a blocking wait, so `stop` is noticed
                # even on a keyboard that never reports anything again.
                for key, _mask in selector.select(timeout=0.1):
                    device = key.fileobj
                    try:
                        for event in device.read():
                            if event.type == evdev_source.ecodes.EV_KEY:
                                self._raw.emit(int(event.code), int(event.value))
                    except OSError:
                        # Unplugged mid-grab; keep listening to the others.
                        selector.unregister(device)
        finally:
            selector.close()

    def _on_raw(self, code: int, value: int) -> None:
        """One key event, now on the Qt thread. Also the whole state machine."""
        if not self.active:
            # Queued from the reader thread just as this stopped. The keys
            # belong to whatever comes next, not to a grab that is over.
            return
        token = evdev_source._token_for(code)
        if token is None:
            return

        # Someone is using the keyboard, so they have not walked away from it.
        self._idle.start()

        if token in MODIFIER_TOKENS:
            if value == 1:
                self._held.add(token)
            elif value == 0:
                self._held.discard(token)
            # A trailing "+" is what makes a held ctrl look like the start of a
            # shortcut rather than a finished one.
            self.progress.emit((canonical(self._held) + "+") if self._held else "")
            return

        # value 2 is the keyboard's auto-repeat, not a second press.
        if value != 1:
            return

        combination = canonical(self._held, token)
        # Stopped before the combination goes out, so the keyboard is already
        # back with the desktop by the time anything reacts to it.
        self.stop()
        self.captured.emit(combination)

    def _timed_out(self) -> None:
        self.stop(tr("Stopped listening -- no key was pressed."))
