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

import json
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..actions import handler_class, registered_types
from ..app import MacroKeyApp
from ..config import (
    GESTURES,
    KEY_COUNT,
    LAYER_COUNT,
    Action,
    HostAction,
    Profile,
    keycodes,
)
from ..device import ChordEvent, DeviceError, HostEvent, KeyEvent, candidates

# Action kinds offered in the slot editor, with the field each one needs.
EDITABLE_KINDS: dict[str, tuple[str, str]] = {
    "none": ("", "Nothing"),
    "key": ("hotkey", "Shortcut, e.g. ctrl+shift+p"),
    "consumer": ("usage", "Media key"),
    "mouse_button": ("button", "Mouse button"),
    "layer_momentary": ("layer", "Layer while held"),
    "layer_toggle": ("layer", "Layer to toggle"),
    "host": ("token", "Host action to run on this PC"),
}

#: Lines kept in the event log. Old ones scroll out rather than growing forever.
LOG_LINES = 500
#: How long a key press stays highlighted in the slot grid.
PRESS_FLASH_MS = 450
#: Base look of a slot button; the press flash appends to this and restores it.
SLOT_STYLE = "text-align: left; padding: 4px 8px;"


def _value_of(action: Action) -> str:
    """The single editable field for this action kind, as text."""
    field, _ = EDITABLE_KINDS.get(action.kind, ("", ""))
    return str(getattr(action, field)) if field else ""


def slot_label(profile: Profile, action: Action) -> str:
    """What a slot does, resolving a host token to the action behind it."""
    if action.kind == "host":
        spec = profile.host_actions.get(action.token)
        name = spec.describe() if spec is not None else "unbound"
        return f"host #{action.token}: {name}"
    return action.describe()


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
        self.kind.currentTextChanged.connect(self._kind_changed)

        self.value = QLineEdit(_value_of(current))
        self.value.setMinimumWidth(240)
        # Where the valid values are a known set, pick from it instead of
        # typing an opaque number and finding out at push time.
        self.choice = QComboBox()
        self.choice.setMinimumWidth(240)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(mid);")

        # Both editors share one labelled row; only one is ever visible, so the
        # label does not need to be shown and hidden along with them.
        value_row = QWidget()
        value_layout = QVBoxLayout(value_row)
        value_layout.setContentsMargins(0, 0, 0, 0)
        value_layout.addWidget(self.value)
        value_layout.addWidget(self.choice)

        form = QFormLayout()
        form.addRow("Action", self.kind)
        form.addRow("Value", value_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        record = buttons.addButton("Record...", QDialogButtonBox.ActionRole)
        record.clicked.connect(self._record)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.hint)
        layout.addWidget(buttons)

        self._kind_changed(self.kind.currentText(), initial=current)

    def _options(self, kind: str) -> list[tuple[str, object]] | None:
        """``[(shown, stored)]`` when this kind has a fixed set of values."""
        if kind == "consumer":
            return [(name, name) for name in sorted(keycodes.CONSUMER_USAGES)]
        if kind == "mouse_button":
            return [(name, name) for name in keycodes.MOUSE_BUTTONS]
        if kind in ("layer_momentary", "layer_toggle"):
            return [
                (f"{index} - {self.app.profile.layers[index].name or index}", index)
                for index in range(LAYER_COUNT)
            ]
        if kind == "host":
            actions = self.app.profile.host_actions
            return [
                (f"{token} - {actions[token].describe()}", token) for token in sorted(actions)
            ] or [("(no host actions yet - add one in the Host Actions tab)", 0)]
        return None

    def _kind_changed(self, kind: str, initial: Action | None = None) -> None:
        _, description = EDITABLE_KINDS[kind]
        options = self._options(kind)
        field, _ = EDITABLE_KINDS[kind]

        self.choice.clear()
        for shown, stored in options or []:
            self.choice.addItem(shown, stored)
        if options is not None and initial is not None and field:
            index = self.choice.findData(getattr(initial, field))
            if index >= 0:
                self.choice.setCurrentIndex(index)

        self.choice.setVisible(options is not None)
        self.value.setVisible(options is None and bool(field))
        self.hint.setText(description)

    def _accept(self) -> None:
        kind = self.kind.currentText()
        field, _ = EDITABLE_KINDS[kind]
        try:
            if not field:
                action = Action()
            elif self._options(kind) is not None:
                action = Action(kind=kind, **{field: self.choice.currentData()})
            else:
                action = Action(kind=kind, **{field: self.value.text().strip()})
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


class HostActionsPage(QWidget):
    """Edits the work behind the host tokens.

    The form is built from each handler's ``param_spec``, so registering a new
    action type gives it an editor here without touching this file.
    """

    changed = Signal()

    def __init__(self, app: MacroKeyApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self._token: int | None = None
        self._fields: dict[str, QWidget] = {}

        self.list = QListWidget()
        self.list.setFixedWidth(240)
        self.list.currentItemChanged.connect(lambda item, _prev: self._select(item))

        add = QPushButton("Add")
        add.clicked.connect(self._add)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(add)
        list_buttons.addWidget(remove)

        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        left.addLayout(list_buttons)

        self.token = QSpinBox()
        self.token.setRange(0, 255)
        self.name = QLineEdit()
        self.type = QComboBox()
        for type_name, label in registered_types().items():
            self.type.addItem(f"{type_name} - {label}", type_name)
        self.type.currentIndexChanged.connect(lambda _index: self._rebuild_form())

        self.params = QFormLayout()
        self.used_by = QLabel()
        self.used_by.setWordWrap(True)
        self.used_by.setStyleSheet("color: palette(mid);")

        test = QPushButton("Test run")
        test.clicked.connect(self._test)
        apply = QPushButton("Apply")
        apply.clicked.connect(self._apply)
        form_buttons = QHBoxLayout()
        form_buttons.addStretch(1)
        form_buttons.addWidget(test)
        form_buttons.addWidget(apply)

        head = QFormLayout()
        head.addRow("Token", self.token)
        head.addRow("Name", self.name)
        head.addRow("Type", self.type)

        self.editor = QWidget()
        right = QVBoxLayout(self.editor)
        right.addLayout(head)
        right.addLayout(self.params)
        right.addWidget(self.used_by)
        right.addStretch(1)
        right.addLayout(form_buttons)

        row = QHBoxLayout(self)
        row.addLayout(left)
        row.addWidget(self.editor, 1)

        self.reload()

    # ------------------------------------------------------------------ list --

    def reload(self) -> None:
        """Repopulates the list, keeping the selected token where possible."""
        wanted = self._token
        self.list.blockSignals(True)
        self.list.clear()
        for token, spec in sorted(self.app.profile.host_actions.items()):
            self.list.addItem(f"{token:>4}  {spec.describe()}")
            self.list.item(self.list.count() - 1).setData(Qt.UserRole, token)
        self.list.blockSignals(False)

        for index in range(self.list.count()):
            if self.list.item(index).data(Qt.UserRole) == wanted:
                self.list.setCurrentRow(index)
                return
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._token = None
            self.editor.setEnabled(False)

    def _select(self, item) -> None:
        if item is None:
            return
        self._token = item.data(Qt.UserRole)
        spec = self.app.profile.host_actions.get(self._token)
        if spec is None:
            return
        self.editor.setEnabled(True)
        self.token.setValue(self._token)
        self.name.setText(spec.name)
        index = self.type.findData(spec.type)
        self.type.blockSignals(True)
        self.type.setCurrentIndex(index if index >= 0 else 0)
        self.type.blockSignals(False)
        self._rebuild_form(spec.params)

    def _add(self) -> None:
        token = self.app.profile.next_host_token()
        self.app.profile.host_actions[token] = HostAction(type="noop", name=f"Action {token}")
        self._token = token
        self.app.save()
        self.reload()
        self.changed.emit()

    def _remove(self) -> None:
        if self._token is None:
            return
        bindings = self._bindings(self._token)
        question = f"Remove host action #{self._token}?"
        if bindings:
            question += "\n\nStill used by: " + ", ".join(bindings)
        if QMessageBox.question(self, "Remove", question) != QMessageBox.Yes:
            return
        self.app.profile.host_actions.pop(self._token, None)
        self._token = None
        self.app.save()
        self.reload()
        self.changed.emit()

    # ------------------------------------------------------------------ form --

    def _rebuild_form(self, params: dict | None = None) -> None:
        """Draws the fields the selected type declares."""
        if params is None:  # the type changed: keep what the new type also uses
            params = self._read_params(strict=False)
        while self.params.rowCount():
            self.params.removeRow(0)
        self._fields.clear()

        handler = handler_class(self.type.currentData())
        for spec in handler.param_spec if handler else ():
            value = params.get(spec.key, spec.default)
            if spec.kind == "bool":
                widget: QWidget = QCheckBox()
                widget.setChecked(bool(value))
            elif spec.kind == "int":
                widget = QSpinBox()
                widget.setRange(0, 1_000_000)
                widget.setValue(int(value or 0))
            elif spec.kind in ("json", "multiline"):
                widget = QPlainTextEdit()
                widget.setFixedHeight(110)
                if spec.kind == "multiline":
                    widget.setPlainText(str(value))
                else:
                    widget.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))
            else:
                widget = QLineEdit(str(value))
            self._fields[spec.key] = widget
            self.params.addRow(spec.label, widget)
            if spec.hint:
                hint = QLabel(spec.hint)
                hint.setWordWrap(True)
                hint.setStyleSheet("color: palette(mid); font-size: 11px;")
                self.params.addRow("", hint)

        self._refresh_used_by()

    def _read_params(self, strict: bool = True) -> dict:
        handler = handler_class(self.type.currentData())
        values: dict = {}
        for spec in handler.param_spec if handler else ():
            widget = self._fields.get(spec.key)
            if widget is None:
                continue
            if spec.kind == "bool":
                values[spec.key] = widget.isChecked()
            elif spec.kind == "int":
                values[spec.key] = widget.value()
            elif spec.kind == "multiline":
                values[spec.key] = widget.toPlainText()
            elif spec.kind == "json":
                text = widget.toPlainText().strip()
                try:
                    values[spec.key] = json.loads(text) if text else spec.default
                except json.JSONDecodeError as exc:
                    if strict:
                        raise ValueError(f"{spec.label}: {exc}") from exc
                    values[spec.key] = spec.default
            else:
                values[spec.key] = widget.text()
        return values

    def _bindings(self, token: int) -> list[str]:
        """Slots that fire this token, so the effect of an edit is visible."""
        found = []
        for layer in range(LAYER_COUNT):
            for key in range(KEY_COUNT):
                for gesture in GESTURES:
                    action = self.app.profile.action(layer, key, gesture)
                    if action.kind == "host" and action.token == token:
                        found.append(f"L{layer} key {key + 1} {gesture}")
        for chord in self.app.profile.chords:
            if chord.action.kind == "host" and chord.action.token == token:
                found.append("+".join(str(k + 1) for k in chord.keys))
        return found

    def _refresh_used_by(self) -> None:
        bindings = self._bindings(self.token.value())
        self.used_by.setText(
            "Bound to: " + ", ".join(bindings)
            if bindings
            else "Not bound to any key yet - pick this token from a slot in a layer tab."
        )

    # --------------------------------------------------------------- commands --

    def _current_spec(self) -> HostAction:
        return HostAction(
            type=self.type.currentData(),
            name=self.name.text().strip(),
            params=self._read_params(),
        )

    def _apply(self) -> None:
        try:
            spec = self._current_spec()
        except ValueError as exc:
            QMessageBox.critical(self, "Invalid parameter", str(exc))
            return

        token = self.token.value()
        actions = self.app.profile.host_actions
        if token != self._token and token in actions:
            QMessageBox.critical(self, "Token in use", f"Token {token} is already taken.")
            return
        if self._token is not None and token != self._token:
            actions.pop(self._token, None)
        actions[token] = spec
        self._token = token
        self.app.save()
        self.reload()
        self.changed.emit()

    def _test(self) -> None:
        try:
            spec = self._current_spec()
        except ValueError as exc:
            QMessageBox.critical(self, "Invalid parameter", str(exc))
            return
        # Runs on the action runner's own thread, exactly as a key press would,
        # so what is tested here is what the key will do.
        self.app.actions.run(spec)


class MainWindow(QMainWindow):
    # Worker threads emit these; Qt delivers them on the GUI thread.
    statusMessage = Signal(str)
    failed = Signal(str, str)
    pulled = Signal(object)
    connectionChanged = Signal()
    # The serial reader thread raises this one for every device event.
    deviceEvent = Signal(object)

    def __init__(self, port: str = "") -> None:
        super().__init__()
        self.app = MacroKeyApp(status=self.statusMessage.emit)
        self.buttons: dict[tuple[int, int, str], QPushButton] = {}
        self._connecting = False

        self.setWindowTitle(f"Maduinos macroKey v{__version__}")
        self.setMinimumSize(900, 700)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 6)
        layout.addWidget(self._build_toolbar(port))

        # The grid and the log share the window: you edit a slot at the top and
        # watch the key you just changed report back at the bottom.
        split = QSplitter(Qt.Vertical)
        split.addWidget(self._build_layers())
        split.addWidget(self._build_log())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        self.setCentralWidget(central)

        self.statusBar().showMessage("Not connected")
        self.statusMessage.connect(self.statusBar().showMessage)
        self.statusMessage.connect(self._log_status)
        self.failed.connect(self._show_error)
        self.pulled.connect(self._adopt)
        self.connectionChanged.connect(self._refresh_connection)
        self.deviceEvent.connect(self._log_event)
        self.app.on_event(self.deviceEvent.emit)

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
                    button.setStyleSheet(SLOT_STYLE)
                    button.clicked.connect(
                        lambda _checked=False, layer=layer, key=key, gesture=gesture: self._edit(
                            layer, key, gesture
                        )
                    )
                    grid.addWidget(button, key + 1, index + 1)
                    self.buttons[(layer, key, gesture)] = button

            grid.setRowStretch(KEY_COUNT + 1, 1)
            self.tabs.addTab(page, self.app.profile.layers[layer].name or f"Layer {layer}")

        self.host_page = HostActionsPage(self.app)
        self.host_page.changed.connect(self._refresh_all)
        self.tabs.addTab(self.host_page, "Host Actions")
        return self.tabs

    def _build_log(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 6, 0, 0)

        self.pause_log = QCheckBox("Pause")
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.log.clear())

        header = QHBoxLayout()
        header.addWidget(QLabel("Event log"))
        header.addStretch(1)
        header.addWidget(self.pause_log)
        header.addWidget(clear)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_LINES)
        self.log.setFont(QFont("monospace"))
        self.log.setMinimumHeight(120)

        box.addLayout(header)
        box.addWidget(self.log, 1)
        return panel

    # -------------------------------------------------------------------- log --

    def _append_log(self, text: str) -> None:
        if self.pause_log.isChecked():
            return
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")

    def _log_status(self, message: str) -> None:
        self._append_log(f"·      {message}")

    def _log_event(self, event: object) -> None:
        """Renders one device event, resolving what the slot is bound to."""
        if isinstance(event, KeyEvent):
            self._flash_slot(event)
            where = f"key   {event.key + 1} {event.gesture:<8} L{event.layer}"
            if event.gesture == "holdend":
                self._append_log(f"{where}  (released)")
                return
            self._append_log(f"{where}  -> {self._binding_of(event)}")
        elif isinstance(event, HostEvent):
            spec = self.app.profile.host_actions.get(event.token)
            what = spec.describe() if spec is not None else "NOT BOUND - add it in Host Actions"
            where = f"host  tok={event.token} key {event.key + 1} L{event.layer}"
            self._append_log(f"{where}  -> {what}")
        elif isinstance(event, ChordEvent):
            keys = "+".join(str(key + 1) for key in event.keys)
            self._append_log(f"chord {keys:<10} L{event.layer}")

    def _binding_of(self, event: KeyEvent) -> str:
        if not (0 <= event.layer < LAYER_COUNT and 0 <= event.key < KEY_COUNT):
            return "?"
        if event.gesture not in GESTURES:
            return "?"
        action = self.app.profile.action(event.layer, event.key, event.gesture)
        # The firmware falls back to the layer 0 tap when a slot is empty, so
        # the log has to show the same thing the keypad actually sent.
        if action.is_empty and event.layer != 0 and event.gesture == "tap":
            action = self.app.profile.action(0, event.key, "tap")
            return f"{slot_label(self.app.profile, action)}  (from layer 0)"
        return slot_label(self.app.profile, action)

    def _flash_slot(self, event: KeyEvent) -> None:
        """Lights the slot in the grid that the press just used."""
        gesture = event.gesture if event.gesture in GESTURES else "hold"
        button = self.buttons.get((event.layer, event.key, gesture))
        if button is None:
            return
        button.setStyleSheet(
            SLOT_STYLE + "background: palette(highlight); color: palette(highlighted-text);"
        )
        QTimer.singleShot(PRESS_FLASH_MS, lambda: button.setStyleSheet(SLOT_STYLE))

    # --------------------------------------------------------------- actions --

    def _refresh_all(self) -> None:
        for (layer, key, gesture), button in self.buttons.items():
            action = self.app.profile.action(layer, key, gesture)
            button.setText(slot_label(self.app.profile, action))

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
        self.host_page.app = self.app
        self.brightness.setValue(profile.brightness)
        self.host_page.reload()
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
