"""The editor window.

The only module in the package that imports a UI toolkit, which is what
keeps every other module in the package runnable headless.
"""

from __future__ import annotations

import sys
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..app import MacroKeyApp
from ..config import KEY_COUNT
from ..config.model import EDITABLE_GESTURES
from ..device import DeviceError, candidates
from ..session import RecordingSession
from .describe import describe_binding
from .slot_dialog import SlotDialog
from .widgets import AUTO_PORT, RescanningComboBox

#: How long the pad is told to hold a previewed colour without being spoken to.
#: Someone reading a colour wheel is silent far longer than the device's own
#: three second default, and has not crashed.
PREVIEW_HOLD_MS = 45000


class MainWindow(QMainWindow):
    # Worker threads emit these; Qt delivers them on the GUI thread.
    statusMessage = Signal(str)
    failed = Signal(str, str)
    connectionChanged = Signal()
    pushFinished = Signal()
    recordingChanged = Signal()

    def __init__(self, port: str = "") -> None:
        super().__init__()
        self.app = MacroKeyApp(status=self.statusMessage.emit)
        self.buttons: dict[tuple[int, str], QPushButton] = {}
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
        layout.addWidget(self._build_keys(), 1)

        # The pad's main feature is invisible: nothing about eight buttons
        # suggests that holding one opens a recorder. One line, stated once.
        hint = QLabel(
            "Hold any key on its own for 3 seconds to record into it - the pixel "
            "turns red. Hold the same key again to store what you did."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); padding: 2px 8px;")
        layout.addWidget(hint)
        layout.addWidget(self._build_capture())
        self.setCentralWidget(central)

        self.record_banner = QLabel("  ● RECORDING - hold the same key again to finish  ")
        self.record_banner.setStyleSheet(
            "background: #c0392b; color: white; font-weight: 600; padding: 6px;"
        )
        self.record_banner.setAlignment(Qt.AlignCenter)
        self.record_banner.setVisible(False)
        layout.insertWidget(0, self.record_banner)

        self.statusBar().showMessage("Not connected")
        self.statusMessage.connect(self.statusBar().showMessage)
        self.pushFinished.connect(self._on_push_finished)
        self.failed.connect(self._show_error)
        self.connectionChanged.connect(self._refresh_connection)

        # The link can drop without anyone clicking, so poll the device rather
        # than trusting whatever the last click implied.
        self._connection_timer = QTimer(self)
        self._connection_timer.setInterval(1000)
        self._connection_timer.timeout.connect(self._refresh_connection)
        self._connection_timer.start()

        # Hold-to-record: the pad drives it, this window just reflects it. The
        # session keeps the recording pixel lit from its own thread -- doing it
        # from a QTimer here meant blocking serial calls on the main thread, and
        # a pad that stopped answering froze the window four seconds in five.
        self.session = RecordingSession(self.app, on_change=self.recordingChanged.emit)
        self.app.session = self.session
        self.recordingChanged.connect(self._refresh_recording)

        self._refresh_all()
        self._refresh_connection()
        # Connect straight away rather than making someone press a button to
        # reach a device that is already plugged in and already identified.
        QTimer.singleShot(0, self._autoconnect)

    # ------------------------------------------------------------------ build --

    def _build_toolbar(self, port: str) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        # Auto-connect picks the port; it does not get to be the only thing that
        # can. Two boards plugged in, a board that answers slowly, a port that
        # has to be named by hand -- when the guess is wrong there has to be
        # somewhere to say so, and a window that connects itself and offers no
        # way to change its mind is worse than one that asks.
        #
        # Empty means "let discovery choose", which is the normal state.
        self.port_box = RescanningComboBox(self._rescan_ports)
        self.port_box.setEditable(True)
        self.port_box.setMinimumWidth(150)
        self.port_box.setToolTip(
            "Leave as Auto to use whichever board identifies itself as a keypad."
        )
        self._rescan_ports()
        self.port_box.setCurrentText(port or self.app.settings.port or AUTO_PORT)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._toggle_connection)

        self.link_label = QLabel()
        self.link_label.setStyleSheet("color: palette(mid);")

        row.addWidget(QLabel("Port"))
        row.addWidget(self.port_box)
        row.addWidget(self.connect_button)
        row.addWidget(self.link_label)
        # No Save, no Write, no Read. Every edit saves and writes itself, and
        # connecting reconciles what is on the pad, so all three buttons could
        # only ever repeat work that had already happened -- or be forgotten,
        # which is worse, because then the key does not do what the screen says.
        # `macrokey push` and `macrokey pull` remain for the rare case.

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

    def _build_capture(self) -> QWidget:
        """Where the last recording is listed, step by step.

        Hidden until there is one. It is the answer to "that is not what I
        did", and until it existed there was no way to ask that question except
        by guessing.
        """
        self.capture_box = QWidget()
        column = QVBoxLayout(self.capture_box)
        column.setContentsMargins(8, 0, 8, 4)

        self.capture_title = QLabel()
        self.capture_title.setStyleSheet("font-weight: 600;")
        self.capture_list = QListWidget()
        self.capture_list.setMaximumHeight(140)
        self.capture_list.setStyleSheet("font-family: monospace;")

        column.addWidget(self.capture_title)
        column.addWidget(self.capture_list)
        self.capture_box.setVisible(False)
        return self.capture_box

    def _build_keys(self) -> QWidget:
        """The eight keys, and nothing wrapped around them.

        This used to be a tab per layer, then a single tab, then this: eight
        rows and two columns, which is the whole pad.
        """
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
                    lambda _checked=False, key=key, gesture=gesture: self._edit(
                        key, gesture
                    )
                )
                grid.addWidget(button, key + 1, index + 1)
                self.buttons[(key, gesture)] = button

        # The resting colour is what the pixel spends most of its time saying,
        # and it was the one part of the profile the editor wrote but never let
        # anyone change.
        grid.addWidget(QLabel("LED"), KEY_COUNT + 1, 0)
        swatch = QPushButton()
        swatch.setToolTip("Colour the pad rests at when nothing is happening.")
        swatch.clicked.connect(self._edit_resting_color)
        grid.addWidget(swatch, KEY_COUNT + 1, 1, 1, len(EDITABLE_GESTURES))
        self.swatch = swatch

        grid.setRowStretch(KEY_COUNT + 2, 1)
        return page

    def _edit_resting_color(self) -> None:
        """Picks the resting colour, showing it on the real pixel while choosing.

        A hex value tells you nothing about how a colour reads on a diffused
        5 mm pixel at 25% brightness, so the pad previews every intermediate
        colour. The preview is host ambient, which never touches EEPROM: cancel
        and the device is exactly where it started, with no write to undo.
        """
        before = self.app.profile.resting_color
        dialog = QColorDialog(QColor(f"#{before}"), self)
        dialog.setWindowTitle("Resting LED colour")
        dialog.currentColorChanged.connect(self._preview_color)

        self._begin_preview()
        accepted = dialog.exec() == QDialog.Accepted
        chosen = dialog.selectedColor() if accepted else QColor()

        if not chosen.isValid():
            # Cancelled: drop the preview and the profile scene comes straight
            # back, with nothing written anywhere.
            self._end_preview()
            return

        value = f"{chosen.red():02x}{chosen.green():02x}{chosen.blue():02x}"
        self.app.profile.resting_color = value
        self._refresh_swatch()

        # Written before the preview is released, so the pixel never flashes the
        # old colour back at the person who just chose one.
        self._apply(f"Resting LED #{value}")
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

    def _refresh_swatch(self) -> None:
        value = self.app.profile.resting_color
        colour = QColor(f"#{value}")
        # Black reads as "off" rather than as a colour, so say so: an empty
        # black rectangle looks like a rendering failure.
        self.swatch.setText("off" if colour.value() == 0 else f"#{value}")
        text = "#f0f0f0" if colour.value() < 128 else "#101010"
        self.swatch.setStyleSheet(
            f"background-color: #{value}; color: {text}; padding: 4px 8px;"
        )

    # --------------------------------------------------------------- actions --

    def _refresh_all(self) -> None:
        self._refresh_swatch()
        for (key, gesture), button in self.buttons.items():
            action = self.app.profile.action(key, gesture)
            button.setText(describe_binding(self.app.profile, action))
            # Anything that needs this computer running is worth flagging on the
            # grid, not just in the dialog: it is the difference between a pad
            # that works when unplugged from this machine and one that does not.
            needs_host = action.kind == "host"
            button.setStyleSheet(
                "text-align: left; padding: 4px 8px;"
                + ("color: palette(link);" if needs_host else "")
            )

    def _edit(self, key: int, gesture: str) -> None:
        dialog = SlotDialog(self, self.app, key, gesture)
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.recorded_steps:
            where = self.app.assign_recording(dialog.recorded_steps, key, gesture)
            self._refresh_all()
            self._apply(f"Key {key + 1} {gesture} ({where})")
            return
        if dialog.result_action is not None:
            self.app.profile.set_action(key, gesture, dialog.result_action)
            self._refresh_all()
            self._apply(f"Key {key + 1} {gesture}")

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
            self.statusMessage.emit(f"{what} saved. It reaches the keypad on the next connect.")
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

    def _toggle_connection(self, *, quiet: bool = False) -> None:
        """Connects or disconnects. `quiet` reports failure without a dialog.

        Auto-connect at startup must not be able to greet anyone with a modal
        error: the pad may simply not be plugged in, which is not a problem
        worth interrupting for, and the toolbar still offers the button.
        """
        if self._connecting:
            return
        if self.app.device.connected:
            self._disconnect()
            return

        self._connecting = True
        self.connect_button.setEnabled(False)
        self.connect_button.setText("Connecting...")
        # Read on this thread. Touching a widget from the worker is undefined in
        # Qt and deadlocks here in practice, which made auto-connect hang the
        # window before it had finished opening.
        port = self._chosen_port()

        def worker() -> None:
            try:
                self.app.connect(port)
                self.app.settings.port = self.app.device.port
                self.app.settings.save()
                # Anything edited while unplugged is still only on disk. Sending
                # it now is what lets the Write button go: without this, editing
                # offline and plugging in left the pad running the old profile
                # with nothing on screen admitting it.
                if not self.app.device_matches_host():
                    self.app.push_profile()
                    self.statusMessage.emit("Keypad brought up to date")
            except DeviceError as exc:
                if quiet:
                    self.statusMessage.emit(f"No keypad found: {exc.args[0].splitlines()[0]}")
                else:
                    self.failed.emit("Connect failed", str(exc))
            except (ValueError, OSError) as exc:
                self.statusMessage.emit(f"Could not update the keypad: {exc}")
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
        if connected:
            hello = self.app.device.hello
            firmware = f" - firmware {hello.firmware}" if hello is not None else ""
            self.link_label.setText(f"{self.app.device.port}{firmware}")
        else:
            self.link_label.setText("no keypad")

    def _chosen_port(self) -> str:
        """The port to open, with Auto meaning "let discovery decide"."""
        text = self.port_box.currentText().strip()
        return "" if text in ("", AUTO_PORT) else text

    def _rescan_ports(self) -> None:
        """Repopulates the port list, keeping whatever was typed or selected."""
        current = self.port_box.currentText()
        found = [AUTO_PORT, *(item.device for item in candidates())]
        if current and current not in found:
            found.append(current)
        self.port_box.blockSignals(True)
        self.port_box.clear()
        self.port_box.addItems(found)
        self.port_box.setCurrentText(current or AUTO_PORT)
        self.port_box.blockSignals(False)

    def _autoconnect(self) -> None:
        """Opens the obvious device on startup.

        There is one keypad and discovery already knows which port it is, so
        asking someone to pick it and press Connect is a question with one
        answer. Failure is quiet: the toolbar still offers the button.
        """
        if self.app.device.connected:
            return
        self._toggle_connection(quiet=True)

    def _refresh_recording(self) -> None:
        session = self.session
        if session.recording:
            self.statusBar().showMessage(
                f"Recording into key {session.active_key + 1} - hold it again to finish"
            )
        outcome = session.last_outcome
        if outcome is not None and not session.recording:
            self._refresh_all()
            self._show_capture(outcome)
        self.record_banner.setVisible(session.recording)

    def _show_capture(self, outcome) -> None:
        """Lists what the last recording actually caught.

        A recording is authored blind: the pad has no screen and the window need
        not even be open. When what was captured is not what was done there was
        nothing to look at, so the only move was to guess -- and "it moved on
        its own" is not a thing anyone can debug from a status bar.
        """
        steps = self.app.recorder.summary(self.session.last_steps)
        where = outcome.where or outcome.error or "nothing was captured"
        self.capture_title.setText(f"Key {outcome.key + 1} {outcome.gesture} - {where}")
        self.capture_list.clear()
        self.capture_list.addItems(steps or ["(nothing)"])
        self.capture_box.setVisible(True)

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
