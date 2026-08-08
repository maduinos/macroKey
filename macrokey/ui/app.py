"""PySide6 editor.

This is the only module allowed to import a UI toolkit. Everything it does is a
call into :class:`~macrokey.app.MacroKeyApp`, so replacing this view or running
with no view at all costs nothing elsewhere.

Work that touches the serial link runs on plain threads, exactly as before, and
reports back through Qt signals. A signal emitted from a worker thread is
delivered on the GUI thread by the event loop, which is what makes it safe to
touch widgets from the slot.
"""

from __future__ import annotations

import os
import sys
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..app import MacroKeyApp
from ..config import KEY_COUNT, LAYER_COUNT, Action, Profile
from ..config.model import EDITABLE_GESTURES
from ..device import DeviceError, candidates

#: How long the device holds a previewed colour without hearing from us. Covers
#: a person deliberating over a colour wheel plus the profile write that
#: follows, and is bounded so a crash cannot park the pixel for good.
PREVIEW_HOLD_MS = 45000

def _daemon_running() -> bool:
    """Whether anything is actually listening on the daemon's state socket.

    Existence is not enough: the socket file outlives the process that made it,
    so a stopped daemon leaves one behind and a check on the path alone reports
    a daemon that is not there. Connecting is the only answer that means
    anything.
    """
    import socket as socket_module

    from ..led import default_socket_path

    if not hasattr(socket_module, "AF_UNIX"):
        return False
    try:
        path = default_socket_path()
        if not path.exists():
            return False
        with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            probe.connect(str(path))
        return True
    except OSError:
        return False


def _nothing_captured_hint() -> str:
    """Why a recording can come back empty, when that has a known cause.

    pynput falls back to its X11 backend under Wayland, where it only sees
    input going to XWayland clients. Typing into a native Wayland window is
    invisible to it, and the recording ends up empty with no explanation.
    """
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return (
            "Nothing was captured. On Wayland, input capture only sees X11 "
            "windows, so typing into most applications is invisible to it. "
            "Recording into a terminal started under XWayland does work."
        )
    return "Nothing was captured. Press Start recording, do the thing, then Stop."


#: A typed run at least this long is worth pointing at before it is stored.
#: Real macros type short things -- a command, a name, a snippet; passwords and
#: pasted tokens are what long unbroken runs usually are.
SECRET_TEXT_LENGTH = 12


def _longest_typed_run(steps) -> int:
    return max(
        (
            len(step.get("params", {}).get("text", ""))
            for step in steps
            if step.get("type") == "text"
        ),
        default=0,
    )


def describe_binding(profile: Profile, action: Action) -> str:
    """What this key does, said the way someone using the pad would say it.

    The grid used to show `action.describe()`, which speaks in the wire format's
    terms -- "host 3", "sequence 1" -- and told you nothing about what pressing
    the key would produce.
    """
    if action.kind == "none":
        return "nothing"
    if action.kind == "layer_momentary":
        name = profile.layers[action.layer].name or f"layer {action.layer}"
        return f"hold for {name}"
    if action.kind == "layer_toggle":
        name = profile.layers[action.layer].name or f"layer {action.layer}"
        return f"toggle {name}"
    if action.kind == "sequence":
        macros = profile.device_macros
        steps = macros[action.slot] if action.slot < len(macros) else []
        return f"recording, {len(steps)} steps (on the keypad)"
    if action.kind == "host":
        spec = profile.host_actions.get(action.token)
        if spec is None:
            return f"missing host action {action.token}"
        return f"recording: {spec.describe()} (needs this computer)"
    return action.describe()


class ShortcutEdit(QLineEdit):
    """Records a shortcut by having it pressed, the way desktop settings do.

    Typing "ctrl+alt+shift+1" means knowing the vocabulary and the separator and
    that it is "gui" rather than "super" here. Pressing the combination needs
    none of that, and it is how every shortcut setting on this desktop already
    works.

    This reads Qt key events rather than capturing globally: the dialog has
    focus while it is open, so the combination arrives here and nowhere else --
    which also means it cannot trigger whatever it is currently bound to.
    """

    QT_MODIFIERS = (
        (Qt.ControlModifier, "ctrl"),
        (Qt.ShiftModifier, "shift"),
        (Qt.AltModifier, "alt"),
        (Qt.MetaModifier, "gui"),
    )
    #: Qt names these differently from macroKey's vocabulary.
    QT_NAMES = {
        Qt.Key_Escape: "esc",
        Qt.Key_Return: "enter",
        Qt.Key_Enter: "enter",
        Qt.Key_Backspace: "backspace",
        Qt.Key_Delete: "delete",
        Qt.Key_Space: "space",
        Qt.Key_Tab: "tab",
        Qt.Key_Up: "up",
        Qt.Key_Down: "down",
        Qt.Key_Left: "left",
        Qt.Key_Right: "right",
        Qt.Key_Home: "home",
        Qt.Key_End: "end",
        Qt.Key_PageUp: "pageup",
        Qt.Key_PageDown: "pagedown",
        Qt.Key_Insert: "insert",
        Qt.Key_CapsLock: "capslock",
        Qt.Key_Print: "printscreen",
    }
    BARE_MODIFIERS = {
        Qt.Key_Control,
        Qt.Key_Shift,
        Qt.Key_Alt,
        Qt.Key_Meta,
        Qt.Key_AltGr,
    }

    changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.capturing = False
        self.setPlaceholderText("ctrl+alt+shift+1")

    def start_capture(self) -> None:
        """Next combination pressed fills the field."""
        self.capturing = True
        self.clear()
        self.setPlaceholderText("Press the shortcut...")
        self.setFocus()

    def stop_capture(self) -> None:
        self.capturing = False
        self.setPlaceholderText("ctrl+alt+shift+1")

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # Typed editing stays available. Capture is a convenience for the common
        # case, not the only way in: some shortcuts are awkward or impossible to
        # press here -- anything the compositor swallows first, or a key this
        # keyboard does not have -- and those still have to be settable.
        if not self.capturing:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key in self.BARE_MODIFIERS:
            # Held on its own it is not a shortcut yet; show it building up.
            self.setText("+".join(self._modifiers(event)) + "+" if self._modifiers(event) else "")
            return

        parts = self._modifiers(event)
        name = self.QT_NAMES.get(key)
        if name is None:
            if Qt.Key_F1 <= key <= Qt.Key_F24:
                name = f"f{key - Qt.Key_F1 + 1}"
            elif 32 < key < 127:
                # event.key() is the key before shift is applied, which is what
                # a shortcut is made of. event.text() is the glyph it produced,
                # so with shift held ctrl+alt+shift+1 arrives as "!" -- a
                # character the keypad has no key for, and one this app cannot
                # parse. The keypad sends the modifiers and the key; the shifted
                # glyph is what the receiving application makes of that.
                name = chr(key).lower()
            else:
                text = event.text()
                name = text.lower() if text and text.isprintable() and len(text) == 1 else ""
        if not name:
            return

        parts.append(name)
        value = "+".join(parts)
        self.setText(value)
        # One combination per press of the button: staying in capture mode would
        # eat the typing of anyone who then wanted to adjust it by hand.
        self.stop_capture()
        self.changed.emit(value)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in self.BARE_MODIFIERS and self.text().endswith("+"):
            self.setText("")

    def _modifiers(self, event) -> list[str]:
        state = event.modifiers()
        return [name for flag, name in self.QT_MODIFIERS if state & flag]


class SlotDialog(QDialog):
    """Everything one key can be, in one window.

    Binding used to be two nested dialogs: pick an action kind and a value in
    one, and if you chose to record, a second window on top of it. The kinds
    were the serial wire format showing through -- key, consumer, mouse_button,
    host -- which is not how anyone thinks about what a key should do.

    There are three answers. Send a shortcut, which is the common one and has to
    be typeable: recording ctrl+alt+shift+5 means pressing it, which fires
    whatever is already bound to it. Replay something, which is authored by
    doing it. Or change layer, the one binding a recording cannot express,
    because it is a state change on the device rather than input to replay.
    """

    captured = Signal(list)
    liveEvent = Signal(str)

    def __init__(self, parent: QWidget, app: MacroKeyApp, layer: int, key: int, gesture: str):
        super().__init__(parent)
        self.app = app
        self.layer, self.key, self.gesture = layer, key, gesture
        self.result_action: Action | None = None
        self.recorded_steps: list[dict] | None = None
        self._recording = False

        self.setWindowTitle(f"Layer {layer} · Key {key + 1} · {gesture}")
        self.setModal(True)
        self.resize(520, 470)

        current = app.profile.action(layer, key, gesture)
        self.now = QLabel(f"Now: {describe_binding(app.profile, current)}")
        self.now.setWordWrap(True)
        self.now.setStyleSheet("font-weight: 600;")

        # ---- shortcut ---------------------------------------------------------
        self.shortcut = ShortcutEdit()
        if current.kind == "key":
            self.shortcut.setText(current.hotkey)
        self.shortcut.changed.connect(lambda _v: self.shortcut.stop_capture())
        press_keys = QPushButton("Press keys")
        press_keys.setToolTip(
            "Fills the field from the next combination pressed. The field can "
            "also just be typed into."
        )
        press_keys.clicked.connect(self.shortcut.start_capture)
        set_shortcut = QPushButton("Set")
        set_shortcut.clicked.connect(self._use_shortcut)
        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(self.shortcut, 1)
        shortcut_row.addWidget(press_keys)
        shortcut_row.addWidget(set_shortcut)

        # ---- recording --------------------------------------------------------
        self.record_button = QPushButton("Start recording")
        self.record_button.clicked.connect(self._toggle_recording)

        self.capture_mouse = QCheckBox("Include mouse")
        self.capture_mouse.setChecked(True)
        self.capture_mouse.setToolTip(
            "Records button clicks and the wheel, not pointer positions: a "
            "replayed click lands wherever the pointer is at the time."
        )

        self.log = QListWidget()
        self.log.setAlternatingRowColors(True)

        self.where = QLabel()
        self.where.setWordWrap(True)
        self.where.setStyleSheet("color: palette(mid);")

        self.use_recording = QPushButton("Use this recording")
        self.use_recording.setEnabled(False)
        self.use_recording.clicked.connect(self._use_recording)

        record_row = QHBoxLayout()
        record_row.addWidget(self.record_button)
        record_row.addWidget(self.capture_mouse)
        record_row.addStretch(1)
        record_row.addWidget(self.use_recording)

        # ---- layer ------------------------------------------------------------
        self.layer_choice = QComboBox()
        for target in range(LAYER_COUNT):
            if target == layer:
                continue
            name = app.profile.layers[target].name or f"Layer {target}"
            self.layer_choice.addItem(f"Hold for {name}", (target, "layer_momentary"))
            self.layer_choice.addItem(f"Toggle {name}", (target, "layer_toggle"))
        if current.kind in ("layer_momentary", "layer_toggle"):
            index = self.layer_choice.findData((current.layer, current.kind))
            if index >= 0:
                self.layer_choice.setCurrentIndex(index)
        use_layer = QPushButton("Set")
        use_layer.clicked.connect(self._use_layer)
        layer_row = QHBoxLayout()
        layer_row.addWidget(self.layer_choice, 1)
        layer_row.addWidget(use_layer)

        # ---- bottom -----------------------------------------------------------
        clear = QPushButton("Clear this key")
        clear.clicked.connect(self._clear)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(clear)
        bottom.addStretch(1)
        bottom.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(self.now)
        layout.addSpacing(8)
        layout.addWidget(_heading("Send a shortcut"))
        layout.addLayout(shortcut_row)
        layout.addSpacing(10)
        layout.addWidget(_heading("Or replay something you do"))
        layout.addLayout(record_row)
        layout.addWidget(self.log, 1)
        layout.addWidget(self.where)
        layout.addSpacing(10)
        layout.addWidget(_heading("Or change layer while this key is used"))
        layout.addLayout(layer_row)
        layout.addSpacing(10)
        layout.addLayout(bottom)

        self.captured.connect(self._show_result)
        self.liveEvent.connect(self._append_live)
        self._show_idle()

    # ------------------------------------------------------------- recording --

    def _show_idle(self) -> None:
        self.log.clear()
        self.log.addItem("Press Start recording, do the thing, then press Stop.")
        self.where.setText(
            "Anything the keypad can replay by itself is stored on the keypad and "
            "keeps working with nothing running on this computer."
        )

    def _toggle_recording(self) -> None:
        if self._recording:
            # Stopping from a button rather than a key: a key would have to be
            # one the macro can never contain, and Esc -- the obvious choice, and
            # what this used to use -- rules out closing a dialog or leaving vim
            # insert mode, which are exactly the things people record.
            self._recording = False
            self.record_button.setText("Start recording")
            self.captured.emit(self.app.stop_recording())
            return

        self.app.recorder.capture_mouse = self.capture_mouse.isChecked()
        # Stopping is a button, so without this the click that ends the
        # recording is its last step, and every replay would finish by
        # clicking wherever that button happened to be.
        self._sync_ignored_region()
        try:
            self.app.start_recording(on_event=self._on_live_event)
        except Exception as exc:  # noqa: BLE001 - pynput failures are environmental
            QMessageBox.critical(self, "Cannot record", str(exc))
            return
        self._recording = True
        self.recorded_steps = None
        self.use_recording.setEnabled(False)
        self.record_button.setText("Stop")
        self.log.clear()
        self.where.setText("Recording. Everything captured appears here as it happens.")

    def _sync_ignored_region(self) -> None:
        frame = self.frameGeometry()
        self.app.recorder.ignore_click_region = (
            frame.x(),
            frame.y(),
            frame.width(),
            frame.height(),
        )

    def moveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # The region is where the Stop button is *now*. Dragging the window
        # mid-recording would otherwise leave it pointing at empty desk, and
        # the click that stops the recording would land in the macro.
        if self._recording:
            self._sync_ignored_region()
        super().moveEvent(event)

    def _on_live_event(self, event) -> None:
        """Called on the listener thread; hop to the GUI thread to touch widgets."""
        char = f" {event.char!r}" if getattr(event, "char", "") else ""
        self.liveEvent.emit(f"{event.kind}  {event.token}{char}")

    def _append_live(self, line: str) -> None:
        self.log.addItem(line)
        self.log.scrollToBottom()

    def _show_result(self, steps: list[dict]) -> None:
        self.recorded_steps = steps
        self.log.clear()
        for line in self.app.recorder.summary(steps):
            self.log.addItem(line)
        self.use_recording.setEnabled(bool(steps))

        if not steps:
            self.log.addItem("(nothing captured)")
            self.where.setText(_nothing_captured_hint())
            return

        if self.app.last_redacted:
            # Say it plainly. A step vanishing without explanation looks like a
            # capture bug, and the person needs to know their password was in
            # range of the recorder so they can judge whether to change it.
            self.log.addItem(
                f"!  {self.app.last_redacted} step(s) removed: a password prompt "
                "was answered while recording"
            )

        typed = _longest_typed_run(steps)
        if typed >= SECRET_TEXT_LENGTH:
            # Capture is global: it sees whatever was typed while it ran, into
            # any window. A long unbroken run of characters is what a password
            # looks like, and it would be stored verbatim in the profile.
            self.log.addItem(f"!  {typed} characters of typed text captured")
            self.where.setText(
                f"This recording contains {typed} characters typed in one run. If any "
                "of that was a password, discard it: recordings are stored as plain "
                "text in the profile."
            )
            return
        if self.app.recorder.device_action(steps) is not None:
            self.where.setText("Will be stored on the keypad. Works with nothing running here.")
        elif self.app.recorder.device_macro(steps) is not None:
            self.where.setText(
                f"Will be stored on the keypad as a {len(steps)} step macro. "
                "Works with nothing running here."
            )
        else:
            # Saying "needs the daemon" is not enough when the daemon is not
            # running: the key would be bound, look bound, and do nothing at
            # all. Check and say which of the two situations this is.
            self.where.setText(
                "Has steps the keypad cannot replay by itself -- text too long "
                "for its macro slots, or characters it has no keys for. "
                + (
                    "The macroKey daemon is running, so it will work."
                    if _daemon_running()
                    else "The macroKey daemon is NOT running, so this key will do "
                    "nothing until it is started:  systemctl --user start macrokey"
                )
            )

    # --------------------------------------------------------------- choices --

    def _use_shortcut(self) -> None:
        text = self.shortcut.text().strip()
        if not text:
            return
        try:
            action = Action(kind="key", hotkey=text)
            action.encode()  # rejects here rather than at write time
        except Exception as exc:  # noqa: BLE001 - report any validation problem
            QMessageBox.critical(self, "That is not a shortcut this keypad can send", str(exc))
            return
        self.result_action = action
        self.accept()

    def _use_recording(self) -> None:
        if not self.recorded_steps:
            return
        self.accept()

    def _use_layer(self) -> None:
        target, kind = self.layer_choice.currentData()
        self.result_action = Action(kind=kind, layer=target)
        self.accept()

    def _clear(self) -> None:
        self.result_action = Action()
        self.accept()

    def reject(self) -> None:
        if self._recording:
            self._recording = False
            self.app.stop_recording()
        super().reject()


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: 600; color: palette(mid);")
    return label


class _RescanningComboBox(QComboBox):
    """A combo box that refreshes itself when it is opened."""

    def __init__(self, rescan) -> None:
        super().__init__()
        self._rescan = rescan

    def showPopup(self) -> None:  # noqa: N802 - Qt naming
        self._rescan()
        super().showPopup()


class MainWindow(QMainWindow):
    # Worker threads emit these; Qt delivers them on the GUI thread.
    statusMessage = Signal(str)
    failed = Signal(str, str)
    pulled = Signal(object)
    connectionChanged = Signal()
    pushFinished = Signal()

    def __init__(self, port: str = "") -> None:
        super().__init__()
        self.app = MacroKeyApp(status=self.statusMessage.emit)
        self.buttons: dict[tuple[int, int, str], QPushButton] = {}
        self.swatches: dict[int, QPushButton] = {}
        # Live colour preview state. No timer: the device is told up front how
        # long to hold the colour, so there is nothing to keep alive.
        self._preview_rgb: tuple[int, int, int] | None = None
        self._connecting = False

        self.setWindowTitle(f"Maduinos macroKey v{__version__}")
        self.setMinimumSize(820, 560)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 6)
        layout.addWidget(self._build_toolbar(port))
        layout.addWidget(self._build_layers(), 1)
        self.setCentralWidget(central)

        self.statusBar().showMessage("Not connected")
        self.statusMessage.connect(self.statusBar().showMessage)
        self.pushFinished.connect(self._on_push_finished)
        self.failed.connect(self._show_error)
        self.pulled.connect(self._adopt)
        self.connectionChanged.connect(self._refresh_connection)

        # The link can drop without anyone clicking, so poll the device rather
        # than trusting whatever the last click implied.
        self._connection_timer = QTimer(self)
        self._connection_timer.setInterval(1000)
        self._connection_timer.timeout.connect(self._refresh_connection)
        self._connection_timer.start()

        self._refresh_all()
        self._refresh_connection()

    # ------------------------------------------------------------------ build --

    def _build_toolbar(self, port: str) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        # Rescans when the list drops down. Built once, it was a snapshot from
        # before the pad was plugged in, and the only way to see a new port was
        # to restart the editor.
        self.port_box = _RescanningComboBox(self._rescan_ports)
        self.port_box.setEditable(True)
        self.port_box.setCurrentText(port or self.app.settings.port)
        self.port_box.setMinimumWidth(170)
        self._rescan_ports()

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._toggle_connection)

        row.addWidget(QLabel("Port"))
        row.addWidget(self.port_box)
        row.addWidget(self.connect_button)
        # Kept for offline work and for adopting a profile edited elsewhere;
        # ordinary edits no longer need either of them.
        self.device_buttons: list[QPushButton] = []
        for text, slot, needs_device in (
            ("Save", self._save, False),
            ("Write to device", self._push, True),
            ("Read from device", self._pull, True),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
            if needs_device:
                self.device_buttons.append(button)

        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 255)
        self.brightness.setValue(self.app.profile.brightness)
        self.brightness.setFixedWidth(120)
        self.brightness.valueChanged.connect(self._brightness_changed)
        self.brightness.sliderReleased.connect(self._brightness_settled)

        self.brightness_value = QLabel()
        self.brightness_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # Sized for the widest value it will ever hold, so dragging the slider
        # does not shove the toolbar around.
        self.brightness_value.setFixedWidth(
            self.brightness_value.fontMetrics().horizontalAdvance("255") + 8
        )
        self.brightness_value.setNum(self.brightness.value())

        version = QLabel(f"v{__version__}")
        version.setStyleSheet("color: palette(mid);")

        row.addStretch(1)
        row.addWidget(QLabel("Brightness"))
        row.addWidget(self.brightness)
        row.addWidget(self.brightness_value)
        row.addSpacing(12)
        row.addWidget(version)
        return bar

    def _build_layers(self) -> QWidget:
        self.tabs = QTabWidget()
        for layer in range(LAYER_COUNT):
            page = QWidget()
            grid = QGridLayout(page)
            grid.setContentsMargins(8, 8, 8, 8)

            for column, title in enumerate(("Key", *(g.title() for g in EDITABLE_GESTURES))):
                header = QLabel(title)
                header.setStyleSheet("font-weight: 600;")
                grid.addWidget(header, 0, column)
                if column:
                    grid.setColumnStretch(column, 1)

            for key in range(KEY_COUNT):
                grid.addWidget(QLabel(str(key + 1)), key + 1, 0)
                for index, gesture in enumerate(EDITABLE_GESTURES):
                    button = QPushButton("-")
                    button.setStyleSheet("text-align: left; padding: 4px 8px;")
                    button.clicked.connect(
                        lambda _checked=False, layer=layer, key=key, gesture=gesture: self._edit(
                            layer, key, gesture
                        )
                    )
                    grid.addWidget(button, key + 1, index + 1)
                    self.buttons[(layer, key, gesture)] = button

            # The layer colour is what the pixel spends most of its time saying,
            # and it was the one part of the profile the editor wrote but never
            # let anyone change. It belongs on the layer, not on a slot: the
            # firmware reads one colour per layer, so editing it per key would
            # offer a choice the device cannot honour.
            grid.addWidget(QLabel("LED"), KEY_COUNT + 1, 0)
            swatch = QPushButton()
            swatch.setToolTip(
                "Colour shown while this layer is active. Layer 0 is normally "
                "left black so an idle pad stays dark."
            )
            swatch.clicked.connect(
                lambda _checked=False, layer=layer: self._edit_layer_color(layer)
            )
            grid.addWidget(swatch, KEY_COUNT + 1, 1, 1, len(EDITABLE_GESTURES))
            self.swatches[layer] = swatch

            grid.setRowStretch(KEY_COUNT + 2, 1)
            self.tabs.addTab(page, self.app.profile.layers[layer].name or f"Layer {layer}")
        return self.tabs

    def _edit_layer_color(self, layer: int) -> None:
        """Picks a layer colour, showing it on the real pixel while choosing.

        A hex value tells you nothing about how a colour reads on a diffused
        5 mm pixel at 25% brightness, so the pad previews every intermediate
        colour. The preview is host ambient, which never touches EEPROM: cancel
        and the device is exactly where it started, with no write to undo.
        """
        before = self.app.profile.layers[layer].keys[0].color
        dialog = QColorDialog(QColor(f"#{before}"), self)
        dialog.setWindowTitle(f"LED colour for layer {layer}")
        dialog.currentColorChanged.connect(self._preview_color)

        self._begin_preview()
        accepted = dialog.exec() == QDialog.Accepted
        chosen = dialog.selectedColor() if accepted else QColor()

        if not chosen.isValid():
            # Cancelled: drop the preview and the profile scene comes straight
            # back, with nothing written anywhere.
            self._end_preview()
            return

        # Stored per key because that is the profile's shape, but written to
        # every key at once so the device and the editor cannot disagree.
        value = f"{chosen.red():02x}{chosen.green():02x}{chosen.blue():02x}"
        for slot in self.app.profile.layers[layer].keys:
            slot.color = value
        self._refresh_swatches()

        # Written before the preview is released, so the pixel never flashes the
        # old colour back at the person who just chose one.
        self._apply(f"Layer {layer} LED #{value}")
        self._end_preview()

    # ------------------------------------------------------------- preview --

    def _begin_preview(self) -> None:
        """Takes the ambient layer for as long as the picker may stay open.

        The device's default deadline is three seconds, which is aimed at a host
        that has crashed. Someone reading a colour wheel is silent for far longer
        than that while being entirely alive, so the window is declared up front
        instead of being defended with keepalives -- there is nothing to say
        between one colour and the next, and traffic sent during a profile write
        would block the GUI thread on the device's request lock.
        """
        if not self.app.device.connected:
            return
        try:
            self.app.device.set_led_mode(True, timeout_ms=PREVIEW_HOLD_MS)
        except DeviceError as exc:
            self.statusMessage.emit(f"Preview unavailable: {exc}")

    def _end_preview(self) -> None:
        self._preview_rgb = None
        if not self.app.device.connected:
            return
        try:
            # Hand the pixel back to the profile scene, so a cancelled edit
            # leaves nothing behind.
            self.app.device.set_led_mode(False)
        except DeviceError:
            pass

    def _preview_color(self, color: QColor) -> None:
        if not color.isValid():
            return
        self._preview_rgb = (color.red(), color.green(), color.blue())
        self._send_preview()

    def _send_preview(self) -> None:
        if self._preview_rgb is None or not self.app.device.connected:
            return
        try:
            self.app.device.set_all(self._preview_rgb)
        except DeviceError as exc:
            self.statusMessage.emit(f"Preview stopped: {exc}")

    def _refresh_swatches(self) -> None:
        for layer, swatch in self.swatches.items():
            value = self.app.profile.layers[layer].keys[0].color
            colour = QColor(f"#{value}")
            # Black reads as "off" rather than as a colour, so say so: an empty
            # black rectangle looks like a rendering failure.
            swatch.setText("off" if colour.value() == 0 else f"#{value}")
            text = "#f0f0f0" if colour.value() < 128 else "#101010"
            swatch.setStyleSheet(
                f"background-color: #{value}; color: {text}; padding: 4px 8px;"
            )

    # --------------------------------------------------------------- actions --

    def _refresh_all(self) -> None:
        self._refresh_swatches()
        for (layer, key, gesture), button in self.buttons.items():
            action = self.app.profile.action(layer, key, gesture)
            button.setText(describe_binding(self.app.profile, action))
            # Anything that needs this computer running is worth flagging on the
            # grid, not just in the dialog: it is the difference between a pad
            # that works when unplugged from this machine and one that does not.
            needs_host = action.kind == "host"
            button.setStyleSheet(
                "text-align: left; padding: 4px 8px;"
                + ("color: palette(link);" if needs_host else "")
            )

    def _edit(self, layer: int, key: int, gesture: str) -> None:
        dialog = SlotDialog(self, self.app, layer, key, gesture)
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.recorded_steps:
            where = self.app.assign_recording(dialog.recorded_steps, layer, key, gesture)
            self._refresh_all()
            self._apply(f"Key {key + 1} {gesture} on layer {layer} ({where})")
            return
        if dialog.result_action is not None:
            self.app.profile.set_action(layer, key, gesture, dialog.result_action)
            self._refresh_all()
            self._apply(f"Key {key + 1} {gesture} on layer {layer}")

    def _apply(self, what: str) -> None:
        """Persists an edit and gets it onto the device.

        Writing the profile takes about 50 ms, so there is no reason to make
        someone remember two toolbar buttons after every change -- and forgetting
        them meant editing a key, pressing it, and getting the old binding with
        nothing on screen saying why. The buttons stay for working offline and
        for pushing a profile edited elsewhere.
        """
        try:
            self.app.save()
        except OSError as exc:
            self.statusMessage.emit(f"Could not save: {exc}")
            return
        if not self.app.device.connected:
            self.statusMessage.emit(f"{what} saved. Connect to write it to the device.")
            return
        try:
            self.app.push_profile()
        except (DeviceError, ValueError) as exc:
            self.statusMessage.emit(f"{what} saved, but the device write failed: {exc}")
            return
        self.statusMessage.emit(f"Done - {what} written to the keypad")

    def _brightness_changed(self, value: int) -> None:
        """Live while dragging. `LED bright=` is a runtime value only."""
        self.app.profile.brightness = int(value)
        self.brightness_value.setNum(int(value))
        if self.app.device.connected:
            try:
                self.app.device.set_brightness(self.app.profile.brightness)
            except DeviceError as exc:
                self.statusMessage.emit(str(exc))

    def _brightness_settled(self) -> None:
        """Persists the level the slider was left at.

        Without this the pad looked right until it was next unplugged, and then
        came back at whatever brightness was last written to EEPROM. Deferred to
        release so dragging does not write the profile on every pixel of travel.
        """
        self._apply(f"Brightness {self.app.profile.brightness}")

    def _in_background(self, work) -> None:
        threading.Thread(target=work, daemon=True).start()

    def _toggle_connection(self) -> None:
        if self._connecting:
            return
        if self.app.device.connected:
            self._disconnect()
            return

        self._connecting = True
        self.connect_button.setEnabled(False)
        self.connect_button.setText("Connecting...")

        def worker() -> None:
            try:
                self.app.connect(self.port_box.currentText())
                self.app.settings.port = self.app.device.port
                self.app.settings.save()
            except DeviceError as exc:
                self.failed.emit("Connect failed", str(exc))
            finally:
                self._connecting = False
                self.connectionChanged.emit()

        self._in_background(worker)

    def _disconnect(self) -> None:
        self.app.disconnect()
        self.statusBar().showMessage("Disconnected")
        self._refresh_connection()

    def _refresh_connection(self) -> None:
        """Keeps the toggle honest even when the link drops on its own."""
        if self._connecting:
            # An empty port means probing every candidate in turn, and each
            # candidate is briefly open before it fails to answer. Reading
            # `connected` here would flip the button to Disconnect mid-probe
            # and let a second click land on a connection that is not real.
            return

        connected = self.app.device.connected
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.connect_button.setEnabled(True)
        self.port_box.setEnabled(not connected)
        # Offering these while there is nothing to talk to only ever produced an
        # error dialog a moment later.
        for button in self.device_buttons:
            button.setEnabled(connected)

    def _rescan_ports(self) -> None:
        """Repopulates the port list, keeping whatever was typed or selected."""
        current = self.port_box.currentText()
        found = [item.device for item in candidates()]
        if current and current not in found:
            found.append(current)
        self.port_box.blockSignals(True)
        self.port_box.clear()
        self.port_box.addItems(found)
        self.port_box.setCurrentText(current)
        self.port_box.blockSignals(False)

    def _save(self) -> None:
        self.app.save()

    def _push(self) -> None:
        """Writes the profile to the device, in the background.

        Takes no arguments, and that is load-bearing: Qt's clicked signal
        carries a `checked` boolean, and while this had an optional callback
        parameter the toolbar button passed False into it. Every write started
        from the toolbar then ended in "'bool' object is not callable" once the
        worker finished. A slot wired to a button either takes nothing or takes
        the checked flag on purpose.
        """

        def worker() -> None:
            try:
                self.app.save()
                self.app.push_profile()
            except (DeviceError, ValueError) as exc:
                self.failed.emit("Write failed", str(exc))
            else:
                self.statusMessage.emit("Done - written to the keypad")
            finally:
                self.pushFinished.emit()

        self.statusMessage.emit("Writing to the keypad...")
        self._in_background(worker)

    def _on_push_finished(self) -> None:
        self._end_preview()

    def _pull(self) -> None:
        def worker() -> None:
            try:
                profile = self.app.pull_profile()
            except (DeviceError, ValueError) as exc:
                self.failed.emit("Read failed", str(exc))
                return
            self.pulled.emit(profile)

        self._in_background(worker)

    def _adopt(self, profile: Profile) -> None:
        # Host actions live only on the host, so a device read must not wipe
        # them: the device stores tokens, not the work behind them.
        profile.host_actions = self.app.profile.host_actions
        answer = QMessageBox.question(
            self,
            "Replace local profile?",
            "The device profile will replace the one in this window. Continue?",
        )
        if answer is not QMessageBox.Yes:
            return
        self.app.profile = profile
        self.app.actions.set_profile(profile)
        self.brightness.setValue(profile.brightness)
        self._refresh_all()

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.app.close()
        super().closeEvent(event)


def run_gui(port: str = "") -> int:
    qt_app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(port=port)
    window.show()
    return qt_app.exec()
