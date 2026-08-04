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

import sys
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
from ..config import GESTURES, KEY_COUNT, LAYER_COUNT, Action, Profile, keycodes
from ..device import DeviceError, candidates

# Action kinds offered in the slot editor, with the field each one needs.
EDITABLE_KINDS: dict[str, tuple[str, str]] = {
    "none": ("", "Nothing"),
    "key": ("hotkey", "Shortcut, e.g. ctrl+shift+p"),
    "consumer": ("usage", "Media key"),
    "mouse_button": ("button", "Mouse button"),
    "layer_momentary": ("layer", "Layer while held"),
    "layer_toggle": ("layer", "Layer to toggle"),
    "host": ("token", "Host action token"),
}


def _value_of(action: Action) -> str:
    """The single editable field for this action kind, as text."""
    field, _ = EDITABLE_KINDS.get(action.kind, ("", ""))
    return str(getattr(action, field)) if field else ""


class SlotDialog(QDialog):
    """Edits one (layer, key, gesture) slot."""

    def __init__(self, parent: QWidget, app: MacroKeyApp, layer: int, key: int, gesture: str):
        super().__init__(parent)
        self.app = app
        self.layer, self.key, self.gesture = layer, key, gesture
        self.result_action: Action | None = None
        self.record_requested = False

        self.setWindowTitle(f"Layer {layer} - Key {key + 1} - {gesture}")
        self.setModal(True)

        current = app.profile.action(layer, key, gesture)

        self.kind = QComboBox()
        self.kind.addItems(list(EDITABLE_KINDS))
        self.kind.setCurrentText(current.kind if current.kind in EDITABLE_KINDS else "none")
        self.kind.currentTextChanged.connect(self._refresh_hint)

        self.value = QLineEdit(_value_of(current))
        self.value.setMinimumWidth(240)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(mid);")

        form = QFormLayout()
        form.addRow("Action", self.kind)
        form.addRow("Value", self.value)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        record = buttons.addButton("Record...", QDialogButtonBox.ActionRole)
        record.clicked.connect(self._record)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.hint)
        layout.addWidget(buttons)

        self._refresh_hint()

    def _refresh_hint(self) -> None:
        _, description = EDITABLE_KINDS[self.kind.currentText()]
        extra = ""
        if self.kind.currentText() == "consumer":
            extra = "  (" + ", ".join(sorted(keycodes.CONSUMER_USAGES)) + ")"
        self.hint.setText(description + extra)

    def _accept(self) -> None:
        field, _ = EDITABLE_KINDS[self.kind.currentText()]
        raw = self.value.text().strip()
        try:
            if not field:
                action = Action()
            elif field in ("layer", "token"):
                action = Action(kind=self.kind.currentText(), **{field: int(raw or 0)})
            else:
                action = Action(kind=self.kind.currentText(), **{field: raw})
            action.encode()  # fails now rather than at push time
        except Exception as exc:  # noqa: BLE001 - report any validation problem
            QMessageBox.critical(self, "Invalid action", str(exc))
            return
        self.result_action = action
        self.accept()

    def _record(self) -> None:
        self.record_requested = True
        self.reject()


class RecordDialog(QDialog):
    """Captures input, shows what it understood, then binds it on confirmation."""

    captured = Signal(list)

    def __init__(self, parent: QWidget, app: MacroKeyApp, layer: int, key: int, gesture: str):
        super().__init__(parent)
        self.app = app
        self.layer, self.key, self.gesture = layer, key, gesture
        self.steps: list[dict] = []

        self.setWindowTitle("Record a macro")
        self.setModal(True)
        self.resize(440, 340)

        intro = QLabel(
            "Press Start, do the thing, then press Esc to stop.\n"
            "The LED turns red while recording."
        )

        self.listbox = QListWidget()

        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self._start)
        self.save_button = QPushButton("Bind")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(self.start_button)
        row.addStretch(1)
        row.addWidget(close_button)
        row.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.listbox, 1)
        layout.addLayout(row)

        self.captured.connect(self._show)

    def _start(self) -> None:
        try:
            self.app.start_recording()
        except Exception as exc:  # noqa: BLE001 - pynput failures are environmental
            QMessageBox.critical(self, "Cannot record", str(exc))
            return
        self.start_button.setEnabled(False)
        self.listbox.clear()
        self.listbox.addItem("recording...")
        threading.Thread(target=self._wait_for_stop, daemon=True).start()

    def _wait_for_stop(self) -> None:
        while self.app.recorder.recording:
            threading.Event().wait(0.1)
        self.captured.emit(self.app.stop_recording())

    def _show(self, steps: list[dict]) -> None:
        self.steps = steps
        self.listbox.clear()
        for line in self.app.recorder.summary(steps):
            self.listbox.addItem(line)
        if not steps:
            self.listbox.addItem("(nothing captured)")
        self.start_button.setEnabled(True)
        self.save_button.setEnabled(bool(steps))

    def _save(self) -> None:
        where = self.app.assign_recording(self.steps, self.layer, self.key, self.gesture)
        self.app.save()
        QMessageBox.information(self, "Bound", f"Stored as {where}")
        self.accept()


class MainWindow(QMainWindow):
    # Worker threads emit these; Qt delivers them on the GUI thread.
    statusMessage = Signal(str)
    failed = Signal(str, str)
    pulled = Signal(object)
    connectionChanged = Signal()

    def __init__(self, port: str = "") -> None:
        super().__init__()
        self.app = MacroKeyApp(status=self.statusMessage.emit)
        self.buttons: dict[tuple[int, int, str], QPushButton] = {}
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

        self.port_box = QComboBox()
        self.port_box.setEditable(True)
        self.port_box.addItems([item.device for item in candidates()])
        self.port_box.setCurrentText(port or self.app.settings.port)
        self.port_box.setMinimumWidth(170)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._toggle_connection)

        row.addWidget(QLabel("Port"))
        row.addWidget(self.port_box)
        row.addWidget(self.connect_button)
        for text, slot in (
            ("Save", self._save),
            ("Write to device", self._push),
            ("Read from device", self._pull),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)

        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 255)
        self.brightness.setValue(self.app.profile.brightness)
        self.brightness.setFixedWidth(120)
        self.brightness.valueChanged.connect(self._brightness_changed)

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

            for column, title in enumerate(("Key", *(gesture.title() for gesture in GESTURES))):
                header = QLabel(title)
                header.setStyleSheet("font-weight: 600;")
                grid.addWidget(header, 0, column)
                if column:
                    grid.setColumnStretch(column, 1)

            for key in range(KEY_COUNT):
                grid.addWidget(QLabel(str(key + 1)), key + 1, 0)
                for index, gesture in enumerate(GESTURES):
                    button = QPushButton("-")
                    button.setStyleSheet("text-align: left; padding: 4px 8px;")
                    button.clicked.connect(
                        lambda _checked=False, layer=layer, key=key, gesture=gesture: self._edit(
                            layer, key, gesture
                        )
                    )
                    grid.addWidget(button, key + 1, index + 1)
                    self.buttons[(layer, key, gesture)] = button

            grid.setRowStretch(KEY_COUNT + 1, 1)
            self.tabs.addTab(page, self.app.profile.layers[layer].name or f"Layer {layer}")
        return self.tabs

    # --------------------------------------------------------------- actions --

    def _refresh_all(self) -> None:
        for (layer, key, gesture), button in self.buttons.items():
            action = self.app.profile.action(layer, key, gesture)
            label = action.describe()
            if action.kind == "host":
                spec = self.app.profile.host_actions.get(action.token)
                if spec is not None:
                    label = f"host: {spec.describe()}"
            button.setText(label)

    def _edit(self, layer: int, key: int, gesture: str) -> None:
        dialog = SlotDialog(self, self.app, layer, key, gesture)
        dialog.exec()
        if dialog.record_requested:
            RecordDialog(self, self.app, layer, key, gesture).exec()
        elif dialog.result_action is not None:
            self.app.profile.set_action(layer, key, gesture, dialog.result_action)
        self._refresh_all()

    def _brightness_changed(self, value: int) -> None:
        self.app.profile.brightness = int(value)
        self.brightness_value.setNum(int(value))
        if self.app.device.connected:
            try:
                self.app.device.set_brightness(self.app.profile.brightness)
            except DeviceError as exc:
                self.statusBar().showMessage(str(exc))

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

    def _save(self) -> None:
        self.app.save()

    def _push(self) -> None:
        def worker() -> None:
            try:
                self.app.save()
                self.app.push_profile()
            except (DeviceError, ValueError) as exc:
                self.failed.emit("Write failed", str(exc))

        self._in_background(worker)

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
