"""The editor window.

The only module in the package that imports a UI toolkit, which is what
keeps every other module in the package runnable headless.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, capture_setup
from ..app import MacroKeyApp
from ..config import (
    KEY_COUNT,
    binary,
    export_profile,
    load_profile_file,
    profile_backup_path,
)
from ..config.model import (
    EDITABLE_GESTURES,
    MAX_TEXT_SPEED_MS,
    MIN_TEXT_SPEED_MS,
    default_profile,
    macro_storage_usage,
    text_speed_shown,
    text_speed_stored,
)
from ..device import DeviceError, candidates
from ..session import RecordingSession
from .describe import describe_binding
from .slot_dialog import SlotDialog
from .widgets import AUTO_PORT, RescanningComboBox

#: How long the pad is told to hold a previewed colour without being spoken to.
#: Someone reading a colour wheel is silent far longer than the device's own
#: three second default, and has not crashed.
PREVIEW_HOLD_MS = 45000

#: Reconnect backoff after a link that was working drops, in seconds, doubling
#: to the ceiling (PROTOCOL.md section 5). A pad re-enumerates on every firmware
#: upload and whenever the cable is touched, and the pad is the only way to
#: start a recording -- so a drop used to leave hold-to-record dead until
#: someone noticed the toolbar and pressed Connect, with nothing saying why
#: holding a key for three seconds had stopped doing anything.
#:
#: Only after a link that was established: probing every serial port on a timer
#: would open unrelated devices repeatedly, and opening a port is not free --
#: plenty of boards reset when their port is opened.
RECONNECT_FIRST_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 30.0

log = logging.getLogger(__name__)


def _button_width(button: QPushButton, *labels: str) -> int:
    """What `button` needs to show the widest of `labels` without eliding.

    Asked of the button itself rather than measured by hand, so the padding a
    style puts around a label is whatever that style actually uses.
    """
    current = button.text()
    widest = 0
    for label in labels:
        button.setText(label)
        widest = max(widest, button.sizeHint().width())
    button.setText(current)
    return widest


class MainWindow(QMainWindow):
    # Worker threads emit these; Qt delivers them on the GUI thread.
    statusMessage = Signal(str)
    failed = Signal(str, str)
    connectionChanged = Signal()
    recordingChanged = Signal()
    profileMismatch = Signal()
    profileAdopted = Signal(object)
    syncSucceeded = Signal(str)
    syncFailed = Signal(str)
    captureSetupFinished = Signal(bool, str)
    resetReady = Signal(bool)
    resetFailed = Signal(str)
    liveCapture = Signal(str)

    def __init__(self, port: str = "") -> None:
        super().__init__()
        self.app = MacroKeyApp(status=self.statusMessage.emit)
        self._startup_port = port
        self.buttons: dict[tuple[int, str], QPushButton] = {}
        # Live colour preview state. No timer: the device is told up front how
        # long to hold the colour, so there is nothing to keep alive.
        self._preview_rgb: tuple[int, int, int] | None = None
        self._connecting = False
        self._syncing = False
        self._resetting = False
        self._profile_prompt_open = False
        self._capture_setup_running = False
        self._profiles_diverged = False
        self._closing = False
        # Reconnect state. `_allowed` is cleared by an explicit Disconnect: the
        # link going away because someone asked for it is not something to undo.
        self._reconnect_allowed = False
        self._reconnect_at: float | None = None
        self._reconnect_delay = RECONNECT_FIRST_SECONDS
        self._was_connected = False
        self._io_queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._io_thread: threading.Thread | None = None

        self.setWindowTitle(f"Maduinos macroKey v{__version__}")
        self.setMinimumSize(680, 560)
        self._build_menus()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 6)
        toolbar = self._build_toolbar(port)
        layout.addWidget(toolbar)
        layout.addWidget(self._build_keys(), 1)

        # The pad's main feature is invisible: nothing about eight buttons
        # suggests that holding one opens a recorder. One line, stated once.
        hint = QLabel(
            "Hold any key on its own for 3 seconds to record into it - the pixel "
            "turns red. Hold the same key again to store what you did. "
            "After setup you can quit this app; the pad keeps working as a keyboard."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(window-text); padding: 2px 8px;")
        layout.addWidget(hint)
        layout.addWidget(self._build_capture())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)
        self._scroll = scroll
        self._toolbar = toolbar
        self._central_layout = layout

        self.record_banner = QLabel("  ● RECORDING - hold the same key again to finish  ")
        self.record_banner.setStyleSheet(
            "background: #c0392b; color: white; font-weight: 600; padding: 6px;"
        )
        self.record_banner.setAlignment(Qt.AlignCenter)
        self.record_banner.setVisible(False)
        layout.insertWidget(0, self.record_banner)

        self.storage_label = QLabel()
        self.storage_label.setToolTip(
            "Shared keypad macro storage (keyboard + mouse steps).\n"
            "All 16 slots draw from the same 308-record pool."
        )
        self.statusBar().showMessage("Not connected")
        self.statusBar().addPermanentWidget(self.storage_label)
        self.statusMessage.connect(self.statusBar().showMessage)
        self.failed.connect(self._show_error)
        self.connectionChanged.connect(self._refresh_connection)
        self.profileMismatch.connect(self._resolve_profile_mismatch)
        self.profileAdopted.connect(self._adopt_profile)
        self.syncSucceeded.connect(self._finish_sync)
        self.syncFailed.connect(self._sync_failed)
        self.captureSetupFinished.connect(self._capture_setup_finished)
        self.resetReady.connect(self._finish_reset)
        self.resetFailed.connect(self._reset_failed)

        # The link can drop without anyone clicking, so poll the device rather
        # than trusting whatever the last click implied.
        self._connection_timer = QTimer(self)
        self._connection_timer.setInterval(1000)
        self._connection_timer.timeout.connect(self._poll_connection)
        self._connection_timer.start()

        # Hold-to-record: the pad drives it, this window just reflects it. The
        # session keeps the recording pixel lit from its own thread -- doing it
        # from a QTimer here meant blocking serial calls on the main thread, and
        # a pad that stopped answering froze the window four seconds in five.
        self.session = RecordingSession(
            self.app,
            on_change=self.recordingChanged.emit,
            on_live_event=self._on_live_capture,
        )
        self.app.session = self.session
        self.recordingChanged.connect(self._refresh_recording)
        self.liveCapture.connect(self._append_live_capture)

        # Device writes are debounced and run on one ordered worker. Dragging a
        # slider or stepping a spin box must not issue a full EEPROM transfer for
        # every intermediate value, nor freeze Qt while serial waits for a reply.
        self._text_speed_timer = QTimer(self)
        self._text_speed_timer.setSingleShot(True)
        self._text_speed_timer.setInterval(350)
        self._text_speed_timer.timeout.connect(self._text_speed_settled)
        self._brightness_preview_timer = QTimer(self)
        self._brightness_preview_timer.setSingleShot(True)
        self._brightness_preview_timer.setInterval(35)
        self._brightness_preview_timer.timeout.connect(self._send_brightness_preview)
        self._brightness_save_timer = QTimer(self)
        self._brightness_save_timer.setSingleShot(True)
        self._brightness_save_timer.setInterval(450)
        self._brightness_save_timer.timeout.connect(self._brightness_settled)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(35)
        self._preview_timer.timeout.connect(self._send_preview)

        self._refresh_all()
        self._refresh_connection()
        # Only now do the labels hold their real text -- "no keypad", the
        # brightness readout, "Disconnect" -- so only now is the row as wide as
        # it will ever be. Measured before this, the window's minimum came out
        # 60 px short and the toolbar was squeezed at any UI font above 9 pt.
        self._pin_minimum_width()
        available = self.screen().availableGeometry()
        self.resize(
            max(self.minimumWidth(), min(900, int(available.width() * 0.9))),
            max(self.minimumHeight(), min(700, int(available.height() * 0.9))),
        )
        # Connect straight away rather than making someone press a button to
        # reach a device that is already plugged in and already identified.
        QTimer.singleShot(0, self._autoconnect)
        # Recording under Wayland needs a package and /dev/input access. Offer
        # the one-click fix once the window is up, not before connect: the pad
        # works without it, and a modal during splash feels like a failure.
        QTimer.singleShot(400, self._maybe_fix_capture)
        # After the capture prompt, and only for someone who already records
        # the mouse -- for a keyboard-only macro none of this matters.
        QTimer.singleShot(900, self._offer_flat_pointer_at_startup)

    def _pin_minimum_width(self) -> None:
        """Keeps the window from being narrower than its toolbar.

        The toolbar uses two short rows. We still honour their size hint so text
        is not elided at a large UI font, but no longer force a 1200 px-wide
        window merely to keep one dense row from colliding with itself.
        """
        margins = self._central_layout.contentsMargins()
        row = max(
            self._toolbar.sizeHint().width(),
            self._toolbar.minimumSizeHint().width(),
        )
        self.setMinimumWidth(max(680, row + margins.left() + margins.right()))

    # ------------------------------------------------------------------ build --

    def _build_menus(self) -> None:
        profile_menu = self.menuBar().addMenu("Profile")
        self._profile_menu = profile_menu
        import_action = QAction("Import…", self)
        import_action.triggered.connect(lambda: self._import_profile())
        export_action = QAction("Export…", self)
        export_action.triggered.connect(lambda: self._export_profile())
        restore_action = QAction("Restore previous version…", self)
        restore_action.triggered.connect(lambda: self._restore_profile_backup())
        profile_menu.addAction(import_action)
        profile_menu.addAction(export_action)
        profile_menu.addSeparator()
        profile_menu.addAction(restore_action)

        help_menu = self.menuBar().addMenu("Help")
        mouse_help = QAction("Mouse macro accuracy", self)
        mouse_help.triggered.connect(lambda: self._show_mouse_help())
        gesture_help = QAction("Recording gestures", self)
        gesture_help.triggered.connect(lambda: self._show_gesture_help())
        setup_help = QAction("Recording setup…", self)
        self._setup_help_action = setup_help
        setup_help.triggered.connect(lambda: self._retry_capture_setup())
        help_menu.addAction(mouse_help)
        help_menu.addAction(gesture_help)
        help_menu.addSeparator()
        help_menu.addAction(setup_help)

    def _import_profile(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Import macroKey profile",
            "",
            "macroKey profiles (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            profile = load_profile_file(path)
            self._adopt_local_profile(profile, f"Imported {path}")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.critical(self, "Import failed", str(exc))

    def _export_profile(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Export macroKey profile",
            "macrokey-profile.json",
            "macroKey profiles (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            export_profile(self.app.profile, path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Exported profile to {path}")

    def _restore_profile_backup(self) -> None:
        path = profile_backup_path()
        if not path.exists():
            QMessageBox.information(self, "No previous version", "No profile backup exists yet.")
            return
        answer = QMessageBox.question(
            self,
            "Restore previous profile?",
            "Replace the current local profile with the previous saved version? "
            "The keypad is not changed until you resolve Sync….",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            profile = load_profile_file(path)
            self._adopt_local_profile(profile, "Restored the previous local profile")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.critical(self, "Restore failed", str(exc))

    def _adopt_local_profile(self, profile, message: str) -> None:
        previous = self.app.profile
        self.app.profile = profile
        try:
            self.app.save()
        except OSError as exc:
            self.app.profile = previous
            QMessageBox.critical(self, "Could not save profile", str(exc))
            return
        self._profiles_diverged = self.app.device.connected
        self._refresh_all()
        self._refresh_connection()
        suffix = " — use Sync… to update the keypad" if self._profiles_diverged else ""
        self.statusBar().showMessage(message + suffix)

    def _show_mouse_help(self) -> None:
        QMessageBox.information(
            self,
            "Mouse macro accuracy",
            "Default mouse replay is relative: clicks happen at the current pointer, "
            "and movement starts there. This is the reliable choice.\n\n"
            "The keypad replays a recorded movement over the time it was recorded "
            "over, rather than as one jump, so pointer acceleration affects the "
            "replay the same way it affected your hand. Flat acceleration removes "
            "the variable altogether -- macroKey will offer that next.\n\n"
            "Fixed position is experimental. It homes to the top-left and depends "
            "on the same monitor layout, scaling, pointer speed/acceleration, window "
            "positions, and application state. Test fixed-position macros on a safe "
            "target before assigning them to destructive actions.",
        )
        self._offer_flat_pointer(asked_for=True)

    def _show_gesture_help(self) -> None:
        QMessageBox.information(
            self,
            "Recording gestures",
            "Tap slot: hold a key by itself for 3 seconds.\n\n"
            "Double slot: tap, then press and hold the same key within 250 ms; "
            "keep holding for 3 seconds.\n\n"
            "The pixel turns red while all keyboard input is being captured. Hold "
            "the same key again to save, or use Discard recording in this window.",
        )

    def _build_toolbar(self, port: str) -> QWidget:
        bar = QWidget()
        column = QVBoxLayout(bar)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        connection_row = QHBoxLayout()
        controls_row = QHBoxLayout()
        column.addLayout(connection_row)
        column.addLayout(controls_row)

        # Auto-connect picks the port; it does not get to be the only thing that
        # can. Two boards plugged in, a board that answers slowly, a port that
        # has to be named by hand -- when the guess is wrong there has to be
        # somewhere to say so, and a window that connects itself and offers no
        # way to change its mind is worse than one that asks.
        #
        # Empty means "let discovery choose", which is the normal state.
        self.port_box = RescanningComboBox(self._rescan_ports)
        self.port_box.setEditable(True)
        # Wide enough for a real port name at whatever the UI font is. A flat
        # 150 px fitted "/dev/ttyACM0" at 9 pt and cut it at 13; the slack on
        # top is the frame and the drop-down arrow, which grow with the font.
        metrics = self.port_box.fontMetrics()
        self.port_box.setMinimumWidth(
            max(150, metrics.horizontalAdvance("/dev/ttyACM0") + metrics.height() * 2)
        )
        self.port_box.setToolTip(
            "Leave as Auto to use whichever board identifies itself as a keypad."
        )
        self._rescan_ports()
        self.port_box.setCurrentText(port or self.app.settings.port or AUTO_PORT)

        self.connect_button = QPushButton("Connect")
        # A button elides its label rather than refuse to shrink, so a toolbar
        # that does not fit squeezes it silently. Pin it to the widest text it
        # will ever carry -- the row then demands its real width instead, and
        # the label stops changing size as the state changes.
        self.connect_button.setMinimumWidth(
            _button_width(self.connect_button, "Connecting...", "Disconnect", "Connect")
        )
        self.connect_button.clicked.connect(self._toggle_connection)

        self.link_label = QLabel()
        port_label = QLabel("Port")
        port_label.setBuddy(self.port_box)
        connection_row.addWidget(port_label)
        connection_row.addWidget(self.port_box)
        connection_row.addWidget(self.connect_button)
        connection_row.addWidget(self.link_label)

        self.sync_button = QPushButton("Sync…")
        self.sync_button.setToolTip(
            "Resolve which profile wins when this computer and the keypad differ."
        )
        self.sync_button.clicked.connect(self._resolve_profile_mismatch)
        connection_row.addStretch(1)
        connection_row.addWidget(self.sync_button)

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

        # Replay is faster than the recording was: consecutive characters are
        # merged into one run with no timing kept, and the pad retypes them at a
        # fixed rate. Too fast for anything that has to catch up -- a terminal
        # still starting, a field that validates as you type -- so it is a knob,
        # and it lives on the profile because the pad replays without the app.
        self.text_speed = QSpinBox()
        self.text_speed.setRange(MIN_TEXT_SPEED_MS, MAX_TEXT_SPEED_MS)
        self.text_speed.setSuffix(" ms")
        # Sized for the widest value it will ever hold, the way the brightness
        # readout is. A flat 84 px was fine for "default" and cut "255 ms" off
        # at anything above a 9 pt UI font.
        self.text_speed.setValue(MAX_TEXT_SPEED_MS)
        self.text_speed.setFixedWidth(self.text_speed.sizeHint().width())
        self.text_speed.setValue(text_speed_shown(self.app.profile.text_speed_ms))
        self.text_speed.setToolTip(
            "Pause between characters when the pad replays typed text.\n"
            "5 ms is what the pad does out of the box. Drop it to 1 ms for the\n"
            "fastest replay, or raise it if the receiving window misses the start."
        )
        self.text_speed.valueChanged.connect(self._text_speed_changed)

        # Destructive, and the only control here that is, so it sits apart from
        # the knobs and says so with an ellipsis: nothing happens on the click.
        self.reset_button = QPushButton("Reset\u2026")
        self.reset_button.setMinimumWidth(_button_width(self.reset_button, "Reset\u2026"))
        self.reset_button.setToolTip(
            "Clears every binding and every recorded macro -- on this computer\n"
            "and on the keypad -- and puts the hyper + 1..8 defaults back."
        )
        self.reset_button.clicked.connect(self._reset_everything)

        brightness_label = QLabel("Brightness")
        brightness_label.setBuddy(self.brightness)
        controls_row.addWidget(brightness_label)
        controls_row.addWidget(self.brightness)
        controls_row.addWidget(self.brightness_value)
        controls_row.addSpacing(12)
        typing_label = QLabel("Typing")
        typing_label.setBuddy(self.text_speed)
        controls_row.addWidget(typing_label)
        controls_row.addWidget(self.text_speed)
        controls_row.addStretch(1)

        controls_row.addWidget(self.reset_button)
        return bar

    def _text_speed_changed(self, value: int) -> None:
        stored = text_speed_stored(value)
        if stored == self.app.profile.text_speed_ms:
            return
        self.app.profile.text_speed_ms = stored
        self.statusMessage.emit(f"Typing speed {value} ms per character (saving…)")
        self._text_speed_timer.start()

    def _text_speed_settled(self) -> None:
        self._apply(
            f"Typing speed {text_speed_shown(self.app.profile.text_speed_ms)} ms per character"
        )

    def _build_capture(self) -> QWidget:
        """Recording settings and the capture log.

        Hold-to-record is authored on the pad; this panel only toggles what is
        captured and shows what arrived -- live while recording, then the stored
        steps once it finishes.
        """
        self.capture_box = QWidget()
        column = QVBoxLayout(self.capture_box)
        column.setContentsMargins(8, 0, 8, 4)

        row = QHBoxLayout()
        self.capture_title = QLabel("Recording")
        self.capture_title.setStyleSheet("font-weight: 600;")
        self.capture_setup_button = QPushButton("Setup…")
        self.capture_setup_button.setToolTip(
            "Check or retry permission setup for global keyboard and mouse recording."
        )
        self.capture_setup_button.clicked.connect(self._retry_capture_setup)
        self.capture_mouse = QCheckBox("Include mouse")
        self.capture_mouse.setChecked(bool(self.app.settings.recorder_capture_mouse))
        self.capture_mouse.setToolTip(
            "Clicks, wheel, and pointer movement. By default clicks happen at "
            "the current pointer and movement is relative to it."
        )
        self.capture_mouse.toggled.connect(self._mouse_capture_toggled)

        self.anchor_mouse = QCheckBox("Fixed screen")
        self.anchor_mouse.setChecked(bool(self.app.settings.recorder_anchor_mouse))
        self.anchor_mouse.setEnabled(self.capture_mouse.isChecked())
        self.anchor_mouse.setToolTip(
            "Homes the pointer before recording and replay. Fixed clicks are only "
            "repeatable with the same monitor layout, scaling, pointer speed, and "
            "window positions. Relative/current-pointer replay is safer."
        )
        self.anchor_mouse.toggled.connect(self._anchor_mouse_toggled)

        self.cancel_recording_button = QPushButton("Discard recording")
        self.cancel_recording_button.setToolTip(
            "Stop global capture and discard everything recorded this time."
        )
        self.cancel_recording_button.clicked.connect(self._cancel_recording)
        self.cancel_recording_button.setVisible(False)
        row.addWidget(self.capture_title)
        row.addWidget(self.capture_setup_button)
        row.addStretch(1)
        row.addWidget(self.capture_mouse)
        row.addWidget(self.anchor_mouse)
        row.addWidget(self.cancel_recording_button)

        self.capture_list = QListWidget()
        self.capture_list.setMaximumHeight(140)
        self.capture_list.setStyleSheet("font-family: monospace;")
        self.capture_list.addItem(
            "Hold a pad key for 3 seconds to record. Captured steps appear here."
        )

        column.addLayout(row)
        column.addWidget(self.capture_list)
        return self.capture_box

    def _mouse_capture_toggled(self, checked: bool) -> None:
        self.app.settings.recorder_capture_mouse = checked
        self.app.recorder.capture_mouse = checked
        self.anchor_mouse.setEnabled(checked and not self.session.recording)
        self.app.settings.save()
        if checked:
            self._offer_flat_pointer()

    # ------------------------------------------------- pointer acceleration --

    def _offer_flat_pointer(self, *, asked_for: bool = False) -> None:
        """Offers to remove pointer acceleration from mouse replay.

        The pad sends relative movement, so the desktop decides how far that is
        on screen. An adaptive profile makes that decision depend on speed; the
        firmware replays at the recorded speed so the curve mostly cancels, but
        flat removes the variable and makes replay exact.

        It is someone's desktop preference, so this asks and never assumes, and
        a no is remembered. `asked_for` is the path from the Help menu, which
        both ignores that no and says something when there is nothing to fix.
        """
        profile = capture_setup.pointer_accel_profile()
        if profile is None:
            if asked_for:
                QMessageBox.information(
                    self,
                    "Pointer acceleration",
                    "This desktop does not expose a pointer acceleration setting "
                    "that macroKey can read, so there is nothing to change here.",
                )
            return
        if profile == "flat":
            if asked_for:
                QMessageBox.information(
                    self,
                    "Pointer acceleration",
                    "Pointer acceleration is already flat, which is the setting "
                    "mouse macros replay most accurately under.",
                )
            return
        if not asked_for and self.app.settings.pointer_accel_declined:
            return

        answer = QMessageBox.question(
            self,
            "Pointer acceleration",
            f"Your desktop scales pointer movement by how fast it is "
            f"(acceleration profile: {profile!r}).\n\n"
            "The keypad replays a recorded movement at the speed it was made, "
            "so this largely cancels out. Turning it off removes the variable "
            "entirely and is what makes a mouse macro land exactly where it "
            "was recorded.\n\n"
            "Switch to flat pointer acceleration? It changes how the mouse "
            "feels everywhere, not just in macros. To undo it later:\n\n"
            "    gsettings set org.gnome.desktop.peripherals.mouse "
            "accel-profile 'default'",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            if not asked_for:
                self.app.settings.pointer_accel_declined = True
                self.app.settings.save()
                self.statusBar().showMessage(
                    "Left pointer acceleration alone. Help > Mouse macro accuracy "
                    "offers it again."
                )
            return

        ok, message = capture_setup.set_pointer_accel_flat()
        self.statusBar().showMessage(message)
        if not ok:
            QMessageBox.warning(self, "Pointer acceleration", message)

    def _anchor_mouse_toggled(self, checked: bool) -> None:
        self.app.settings.recorder_anchor_mouse = checked
        self.app.recorder.anchor_mouse = checked
        self.app.settings.save()

    def _cancel_recording(self) -> None:
        if self.session.recording:
            self.session.abort("Recording discarded")

    def _build_keys(self) -> QWidget:
        """The eight keys, and nothing wrapped around them.

        This used to be a tab per layer, then a single tab, then this: eight
        rows and two columns, which is the whole pad.
        """
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
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
                button.setMinimumHeight(button.sizeHint().height())
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

        def worker() -> None:
            try:
                self.app.device.set_led_mode(True, timeout_ms=PREVIEW_HOLD_MS)
            except DeviceError as exc:
                self.statusMessage.emit(f"Preview unavailable: {exc}")

        self._in_background(worker)

    def _end_preview(self) -> None:
        self._preview_timer.stop()
        self._preview_rgb = None
        if self.session.recording:
            # The recording session now owns host LED mode and its red warning.
            # Releasing a colour-picker preview after recording auto-closes
            # would otherwise immediately erase that warning.
            return
        if not self.app.device.connected:
            return

        def worker() -> None:
            try:
                # Hand the pixel back to the profile scene, so a cancelled edit
                # leaves nothing behind.
                self.app.device.set_led_mode(False)
            except DeviceError:
                pass

        self._in_background(worker)

    def _preview_color(self, color: QColor) -> None:
        if not color.isValid():
            return
        self._preview_rgb = (color.red(), color.green(), color.blue())
        self._preview_timer.start()

    def _send_preview(self) -> None:
        if self._preview_rgb is None or not self.app.device.connected:
            return
        rgb = self._preview_rgb

        def worker() -> None:
            try:
                self.app.device.set_all(rgb)
            except DeviceError as exc:
                self.statusMessage.emit(f"Preview stopped: {exc}")

        self._in_background(worker)

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
        self._refresh_toolbar()
        self._refresh_swatch()
        self._refresh_storage()
        for (key, gesture), button in self.buttons.items():
            action = self.app.profile.action(key, gesture)
            button.setText(describe_binding(self.app.profile, action))
            button.setStyleSheet("text-align: left; padding: 4px 8px;")

    def _refresh_toolbar(self) -> None:
        """Puts the profile's own values back on the toolbar widgets.

        They are seeded once when the toolbar is built, so anything that
        replaces the profile wholesale -- adopting the pad's on Pull -- left
        brightness and typing speed showing the values of a profile that is no
        longer loaded. A fresh install is where it bites: the defaults are on
        screen, the pad's are in the profile, and the two never agree again.

        Signals are blocked because these setters would otherwise be read as
        edits and written straight back to the pad.
        """
        for widget, value in (
            (self.brightness, self.app.profile.brightness),
            (self.text_speed, text_speed_shown(self.app.profile.text_speed_ms)),
        ):
            was_blocked = widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(was_blocked)
        self.brightness_value.setNum(self.app.profile.brightness)

    def _refresh_storage(self) -> None:
        used, capacity, used_pct, free_pct = macro_storage_usage(
            self.app.profile.device_macros
        )
        self.storage_label.setText(
            f"Storage {used_pct}% used · {free_pct}% free ({used}/{capacity})"
        )

    def _edit(self, key: int, gesture: str) -> None:
        dialog = SlotDialog(self, self.app, key, gesture)
        if dialog.exec() != QDialog.Accepted:
            self.capture_mouse.blockSignals(True)
            self.capture_mouse.setChecked(bool(self.app.settings.recorder_capture_mouse))
            self.capture_mouse.blockSignals(False)
            self.anchor_mouse.blockSignals(True)
            self.anchor_mouse.setChecked(bool(self.app.settings.recorder_anchor_mouse))
            self.anchor_mouse.setEnabled(self.capture_mouse.isChecked())
            self.anchor_mouse.blockSignals(False)
            self._refresh_connection()
            return
        self.capture_mouse.blockSignals(True)
        self.capture_mouse.setChecked(bool(self.app.settings.recorder_capture_mouse))
        self.capture_mouse.blockSignals(False)
        self.anchor_mouse.blockSignals(True)
        self.anchor_mouse.setChecked(bool(self.app.settings.recorder_anchor_mouse))
        self.anchor_mouse.setEnabled(self.capture_mouse.isChecked())
        self.anchor_mouse.blockSignals(False)
        self._refresh_connection()
        if dialog.result_action is not None:
            self.app.profile.set_action(key, gesture, dialog.result_action)
            self.app.profile.reclaim_storage()
            self._refresh_all()
            self._apply(f"Key {key + 1} {gesture}")

    def _reset_everything(self) -> None:
        """Factory-resets both sides: every binding and recorded macro is gone.

        The pad is cleared with its own RESET rather than by pushing the host
        defaults. ``Profile::writeDefaults`` is the definition the two sides are
        built to agree on -- going through it means they match by construction,
        and it zeroes the macro region outright instead of leaving records that
        nothing points at.

        The pad goes first. If it refuses, the host profile is left alone: two
        sides that still agree beats a cleared computer and a pad that kept
        eight bindings nothing on screen admits to.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Reset everything?")
        box.setText(
            "Every key binding and every recorded macro is cleared -- on this "
            "computer and on the keypad -- and the hyper + 1..8 defaults go "
            "back.\n\nThe previous local profile remains available under "
            "Profile > Restore previous version."
        )
        reset = box.addButton("Reset", QMessageBox.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is not reset:
            self.statusBar().showMessage("Reset cancelled")
            return
        if self.session.recording:
            self.statusBar().showMessage("Finish or discard the recording before resetting")
            return

        connected = self.app.device.connected
        self._resetting = True
        self._refresh_connection()
        if connected:
            self.statusMessage.emit("Resetting the keypad…")

            def worker() -> None:
                try:
                    self.app.device.reset_defaults()
                    self.app.confirm_on_device()
                except (DeviceError, OSError) as exc:
                    self.resetFailed.emit(str(exc))
                    return
                self.resetReady.emit(True)

            self._in_background(worker)
            return
        self._finish_reset(False)

    def _finish_reset(self, device_was_reset: bool) -> None:
        self._resetting = False
        self.app.profile = default_profile()
        self._refresh_all()
        try:
            self.app.save()
        except OSError as exc:
            self.statusMessage.emit(f"The keypad was cleared, but could not save: {exc}")
            self._refresh_connection()
            return

        self._profiles_diverged = False
        if device_was_reset and self.app.device.connected:
            self._profiles_diverged = False
            self.statusBar().showMessage(
                "Reset. The keypad and this computer are back to defaults."
            )
        else:
            self.statusBar().showMessage(
                "Reset this computer. The keypad still holds its own bindings "
                "until you connect and choose Push in Sync…."
            )
        self._refresh_connection()

    def _reset_failed(self, message: str) -> None:
        self._resetting = False
        self._refresh_connection()
        QMessageBox.critical(
            self, "Reset failed", f"The keypad kept its profile: {message}"
        )

    def _apply(self, what: str) -> None:
        """Persists an edit and gets it onto the device.

        Writing the profile takes about 50 ms, so there is no reason to make
        someone remember two toolbar buttons after every change -- and forgetting
        them meant editing a key, pressing it, and getting the old binding with
        nothing on screen saying why. Sync remains only for an explicit choice
        when the computer and keypad hold different profiles.
        """
        try:
            self.app.save()
        except OSError as exc:
            self.statusMessage.emit(f"Could not save: {exc}")
            return
        if not self.app.device.connected:
            self.statusMessage.emit(f"{what} saved. It reaches the keypad on the next connect.")
            return
        if (
            self._connecting
            or self._syncing
            or self._resetting
            or self.session.recording
        ):
            self.statusMessage.emit(
                f"{what} saved locally. The current device operation will finish first."
            )
            return
        if self._profiles_diverged:
            self.statusMessage.emit(
                f"{what} saved locally. Profiles still differ; use Sync… to choose a side."
            )
            return
        try:
            blob = binary.encode_profile(self.app.profile)
        except ValueError as exc:
            self.statusMessage.emit(f"Could not build the keypad profile: {exc}")
            return

        def worker() -> None:
            try:
                self.app.device.write_profile(blob)
                self.app.confirm_on_device()
            except (DeviceError, ValueError, OSError) as exc:
                self.statusMessage.emit(f"{what} saved, but the device write failed: {exc}")
                return
            self.statusMessage.emit(f"Done - {what} written to the keypad")

        self.statusMessage.emit(f"{what} saved; writing to the keypad…")
        self._in_background(worker)

    def _brightness_changed(self, value: int) -> None:
        """Live while dragging. `LED bright=` is a runtime value only."""
        self.app.profile.brightness = int(value)
        self.brightness_value.setNum(int(value))
        self._brightness_preview_timer.start()
        self._brightness_save_timer.start()

    def _send_brightness_preview(self) -> None:
        value = self.app.profile.brightness
        if not self.app.device.connected:
            return

        def worker() -> None:
            try:
                self.app.device.set_brightness(value)
            except DeviceError as exc:
                self.statusMessage.emit(str(exc))

        self._in_background(worker)

    def _brightness_settled(self) -> None:
        """Persists the level the slider was left at.

        Without this the pad looked right until it was next unplugged, and then
        came back at whatever brightness was last written to EEPROM. Deferred to
        release so dragging does not write the profile on every pixel of travel.
        """
        self._brightness_save_timer.stop()
        self._apply(f"Brightness {self.app.profile.brightness}")

    def _in_background(self, work: Callable[[], None]) -> None:
        if self._closing:
            return
        if self._io_thread is None or not self._io_thread.is_alive():
            self._io_thread = threading.Thread(
                target=self._io_worker,
                name="macrokey-ui-io",
                daemon=True,
            )
            self._io_thread.start()
        self._io_queue.put(work)

    def _io_worker(self) -> None:
        """Runs serial/subprocess work in submission order, never on Qt's thread."""
        while True:
            work = self._io_queue.get()
            if work is None:
                return
            if self._closing:
                continue
            try:
                work()
            except Exception:  # noqa: BLE001 - keep later queued work alive
                log.exception("background UI operation failed")

    def _toggle_connection(self, *, quiet: bool = False) -> None:
        """Connects or disconnects. `quiet` reports failure without a dialog.

        Auto-connect at startup must not be able to greet anyone with a modal
        error: the pad may simply not be plugged in, which is not a problem
        worth interrupting for, and the toolbar still offers the button.
        """
        if self._connecting or self._syncing or self._resetting:
            return
        if self.app.device.connected:
            self._disconnect()
            return

        self._connecting = True
        self._refresh_connection()
        # Read on this thread. Touching a widget from the worker is undefined in
        # Qt and deadlocks here in practice, which made auto-connect hang the
        # window before it had finished opening.
        port = self._chosen_port()

        def worker() -> None:
            profiles_differ = False
            try:
                self.app.connect(port)
                # Preserve Auto as Auto. Remembering the resolved /dev path
                # pinned the next launch to a number that changes after every
                # firmware upload or re-enumeration.
                self.app.settings.port = port
                self.app.settings.save()
                # Never silent-overwrite. PROTOCOL requires asking which side wins
                # when the stored profile and the pad disagree.
                profiles_differ = not self.app.device_matches_host()
                self._profiles_diverged = profiles_differ
                if not profiles_differ:
                    self.statusMessage.emit("Connected")
            except DeviceError as exc:
                if quiet:
                    self.statusMessage.emit(f"No keypad found: {exc.args[0].splitlines()[0]}")
                else:
                    self.failed.emit("Connect failed", str(exc))
            except (ValueError, OSError) as exc:
                self.statusMessage.emit(f"Could not update the keypad: {exc}")
            except RuntimeError:
                # Window closed while the worker was still connecting.
                return
            finally:
                self._connecting = False
                try:
                    self.connectionChanged.emit()
                    # A mismatch dialog is meaningful only after the device is
                    # fully identified and the connecting state has ended.
                    if profiles_differ and self.app.device.connected:
                        self.profileMismatch.emit()
                except RuntimeError:
                    pass

        self._in_background(worker)

    def _resolve_profile_mismatch(self) -> None:
        """Ask which profile wins when this computer and the pad disagree."""
        if (
            not self.app.device.connected
            or self._connecting
            or self._syncing
            or self._resetting
            or self.session.recording
        ):
            return
        box = QMessageBox(self)
        box.setWindowTitle("Profile differs")
        box.setText(
            "This computer and the keypad have different profiles.\n\n"
            "Pull: use what is on the keypad.\n"
            "Push: overwrite the keypad with this computer's profile.\n"
            "Cancel: leave both as they are."
        )
        pull = box.addButton("Pull from keypad", QMessageBox.AcceptRole)
        push = box.addButton("Push to keypad", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        self._profile_prompt_open = True
        try:
            box.exec()
        finally:
            self._profile_prompt_open = False
        clicked = box.clickedButton()
        if clicked is not pull and clicked is not push:
            self._profiles_diverged = True
            self._refresh_connection()
            self.statusBar().showMessage(
                "Connected — profiles still differ. Local edits will not overwrite "
                "the keypad until Sync… is resolved."
            )
            return
        if self.session.recording:
            self.statusBar().showMessage(
                "Finish or discard the recording before synchronizing profiles"
            )
            return

        self._syncing = True
        self._refresh_connection()
        if clicked is pull:
            self.statusMessage.emit("Reading the keypad profile…")

            def worker() -> None:
                try:
                    profile = self.app.pull_profile()
                except (DeviceError, ValueError, OSError) as exc:
                    self.syncFailed.emit(str(exc))
                    return
                self.profileAdopted.emit(profile)

        else:
            self.statusMessage.emit("Writing this computer's profile to the keypad…")

            def worker() -> None:
                try:
                    self.app.push_profile()
                except (DeviceError, ValueError, OSError) as exc:
                    self.syncFailed.emit(str(exc))
                    return
                self.syncSucceeded.emit("Keypad updated from this computer")

        self._in_background(worker)

    def _adopt_profile(self, profile) -> None:
        self.app.profile = profile
        try:
            self.app.save()
        except OSError as exc:
            self._sync_failed(f"Read succeeded, but the profile could not be saved: {exc}")
            return
        self._refresh_all()
        self._finish_sync("Adopted the keypad profile")

    def _finish_sync(self, message: str) -> None:
        self._syncing = False
        self._profiles_diverged = False
        self._refresh_connection()
        self.statusBar().showMessage(message)

    def _sync_failed(self, message: str) -> None:
        self._syncing = False
        self._profiles_diverged = True
        self._refresh_connection()
        QMessageBox.critical(self, "Sync failed", message)

    def _disconnect(self) -> None:
        # Asked for, so do not undo it. `_poll_connection` sees the same
        # transition a dropped cable makes and must be able to tell them apart.
        self._reconnect_allowed = False
        self._reconnect_at = None
        self.app.disconnect()
        self.statusBar().showMessage("Disconnected")
        self._refresh_connection()

    # ------------------------------------------------------------ reconnect --

    def _poll_connection(self) -> None:
        """One tick: notice what the link did, then reflect and act on it."""
        connected = self.app.device.connected
        if connected and not self._was_connected:
            # A link worth restoring. Every later drop is now worth chasing, and
            # the backoff starts over.
            self._reconnect_allowed = True
            self._reconnect_delay = RECONNECT_FIRST_SECONDS
            self._reconnect_at = None
        elif self._was_connected and not connected and self._reconnect_allowed:
            self._reconnect_at = time.monotonic() + self._reconnect_delay
            self.statusBar().showMessage("Keypad disconnected; looking for it again…")
        self._was_connected = connected

        self._refresh_connection()
        self._maybe_reconnect()

    def _maybe_reconnect(self) -> None:
        """Retries a dropped link, once its backoff has elapsed.

        The next attempt is scheduled before this one runs rather than after it
        fails: the connect worker reports failure by simply leaving the link
        down, and there is nothing else to hang the growth of the backoff on.
        """
        if self._reconnect_at is None or self._closing:
            return
        if (
            self.app.device.connected
            or self._connecting
            or self._syncing
            or self._resetting
            or self._profile_prompt_open
            or self.session.recording
        ):
            return
        if time.monotonic() < self._reconnect_at:
            return

        self._reconnect_at = time.monotonic() + self._reconnect_delay
        self._reconnect_delay = min(self._reconnect_delay * 2, RECONNECT_MAX_SECONDS)
        self._toggle_connection(quiet=True)

    def _set_editing_enabled(self, enabled: bool) -> None:
        """Locks profile-changing controls during one coherent device operation."""
        self._profile_menu.setEnabled(enabled)
        setup_enabled = enabled and not self._capture_setup_running
        self._setup_help_action.setEnabled(setup_enabled)
        self.brightness.setEnabled(enabled)
        self.text_speed.setEnabled(enabled)
        self.reset_button.setEnabled(enabled)
        self.capture_setup_button.setEnabled(setup_enabled)
        self.capture_mouse.setEnabled(enabled)
        self.anchor_mouse.setEnabled(enabled and self.capture_mouse.isChecked())
        self.swatch.setEnabled(enabled)
        for button in self.buttons.values():
            button.setEnabled(enabled)

    def _refresh_connection(self) -> None:
        """Keeps the toggle honest even when the link drops on its own."""
        if self._connecting:
            # An empty port means probing every candidate in turn, and each
            # candidate is briefly open before it fails to answer. Reading
            # `connected` here would flip the button to Disconnect mid-probe
            # and let a second click land on a connection that is not real.
            self.connect_button.setText("Connecting...")
            self.connect_button.setEnabled(False)
            self.port_box.setEnabled(False)
            self.sync_button.setEnabled(False)
            self._set_editing_enabled(False)
            return

        connected = self.app.device.connected
        self.connect_button.setText("Disconnect" if connected else "Connect")
        recording = self.session.recording
        busy = recording or self._syncing or self._resetting
        self.connect_button.setEnabled(not busy)
        self.port_box.setEnabled(not connected and not busy)
        self.sync_button.setEnabled(connected and not busy)
        self.sync_button.setText("Sync needed…" if self._profiles_diverged else "Sync…")
        self._set_editing_enabled(not busy)
        if connected:
            hello = getattr(self.app.device, "hello", None)
            firmware = f" - firmware {hello.firmware}" if hello is not None else ""
            suffix = " · profiles differ" if self._profiles_diverged else ""
            port = getattr(self.app.device, "port", "keypad")
            self.link_label.setText(f"{port}{firmware}{suffix}")
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
        if not self._startup_port and not self.app.settings.auto_connect:
            self.statusBar().showMessage("Auto-connect is disabled")
            return
        chosen = self._chosen_port()
        if (
            not self._startup_port
            and chosen
            and chosen == self.app.settings.port
            and chosen not in {item.device for item in candidates()}
        ):
            # A remembered /dev number commonly changes after firmware upload.
            # Switch the visible choice to Auto as well; otherwise the worker
            # discovers the pad and then saves the stale number right back.
            self.port_box.setCurrentText(AUTO_PORT)
            self.statusBar().showMessage(f"{chosen} is gone; looking for the keypad")
        self._toggle_connection(quiet=True)

    def _offer_flat_pointer_at_startup(self) -> None:
        if self._closing or not self.app.settings.recorder_capture_mouse:
            return
        self._offer_flat_pointer()

    def _retry_capture_setup(self) -> None:
        self.app.settings.capture_setup_declined = False
        self.app.settings.save()
        self._maybe_fix_capture(force=True)

    def _maybe_fix_capture(self, *, force: bool = False) -> None:
        """First-run (and later) prep so hold-to-record can actually capture.

        Fully silent setup is impossible on Linux: device nodes need root once.
        The potentially minutes-long pip/pkexec work runs on the ordered worker,
        while every dialog and widget update stays on Qt's thread.
        """
        from ..capture_setup import fix_capture, needs_linux_capture_fix, status

        if self._capture_setup_running:
            return
        if (
            self._connecting
            or self._syncing
            or self._resetting
            or self._profile_prompt_open
            or self.session.recording
        ):
            # Startup auto-connect normally lasts longer than the initial 400
            # ms timer. Do not stack a permission dialog on top of connection
            # or profile reconciliation; retry once that interaction settles.
            if not self._closing:
                QTimer.singleShot(750, lambda: self._maybe_fix_capture(force=force))
            return
        if not needs_linux_capture_fix():
            if force:
                self.statusMessage.emit("Recording is ready")
            return
        if self.app.settings.capture_setup_declined and not force:
            return

        before = status()
        detail = before.reason or "Recording cannot see the keyboard on this session."
        answer = QMessageBox.question(
            self,
            "Enable recording?",
            f"{detail}\n\n"
            "Allow macroKey to set this up? You will be asked for your "
            "administrator password once. This grants your account access to "
            "all keyboard and mouse input, including passwords; macroKey opens "
            "that input only while the pixel and banner show recording. The "
            "keypad still works either way — only recording needs this.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if self.session.recording:
            # Starting a pad recording auto-closes modal editor/setup dialogs so
            # the live capture becomes visible. That is postponement, not the
            # user choosing "No" permanently.
            if not self._closing:
                QTimer.singleShot(750, lambda: self._maybe_fix_capture(force=force))
            return
        if answer != QMessageBox.Yes:
            self.app.settings.capture_setup_declined = True
            self.app.settings.save()
            self.statusMessage.emit("Recording setup skipped — hold-to-record will not capture")
            return

        self.app.settings.capture_setup_declined = False
        self.app.settings.save()
        self._capture_setup_running = True
        self._refresh_connection()
        self.statusMessage.emit("Preparing recording support…")

        def worker() -> None:
            try:
                ok, message = fix_capture(grant_devices=True)
            except Exception as exc:  # noqa: BLE001 - surface setup failures in the UI
                ok, message = False, str(exc)
            self.captureSetupFinished.emit(ok, message)

        self._in_background(worker)

    def _capture_setup_finished(self, ok: bool, message: str) -> None:
        self._capture_setup_running = False
        self._refresh_connection()
        if ok:
            self.app.settings.capture_setup_declined = False
            self.app.settings.save()
            self.statusMessage.emit("Recording is ready")
            QMessageBox.information(
                self,
                "Recording ready",
                "Hold a key for 3 seconds to record. "
                "If a brand-new keyboard appears after reboot and recording "
                "fails again, log out and back in once so the input group applies.",
            )
            return

        QMessageBox.warning(
            self,
            "Could not finish setup",
            f"{message}\n\n"
            "You can retry next launch, or run:\n"
            "  sudo usermod -aG input $USER\n"
            "then log out and back in.",
        )

    def _refresh_recording(self) -> None:
        session = self.session
        recording = session.recording
        if session.recording:
            # Recording is intentionally started from the physical pad, often
            # while its slot editor is still open. Dismiss modal children so
            # the red banner and live event log are actually visible. Their
            # callers all treat rejection as Cancel.
            for dialog in self.findChildren(QDialog):
                if dialog.isModal() and dialog.isVisible():
                    dialog.reject()
            active_key = session.active_key
            assert active_key is not None
            key = active_key + 1
            gesture = session.active_gesture
            self.statusBar().showMessage(
                f"Recording into key {key} ({gesture}) - hold it again to finish"
            )
            self.record_banner.setText(
                f"  ● RECORDING key {key} · {gesture} — hold the same key again to finish  "
            )
            self.capture_title.setText(f"Recording key {key} · {gesture}")
            self.capture_list.clear()
            self.capture_list.addItem("(listening…)")
            self._sync_ignored_region()
        outcome = session.last_outcome
        if outcome is not None and not session.recording:
            self.app.recorder.ignore_click_region = None
            self._refresh_all()
            self._show_capture(outcome)
        self.record_banner.setVisible(recording)
        self.cancel_recording_button.setVisible(recording)
        self._refresh_connection()

    def _on_live_capture(self, event) -> None:
        """Recorder thread → GUI thread. Shows that capture is actually alive."""
        char = f" {event.char!r}" if getattr(event, "char", "") else ""
        data = ""
        if event.kind == "mouse_move" and event.data:
            data = f"  {event.data[0]:+d},{event.data[1]:+d}"
        elif event.kind == "scroll" and event.data:
            data = f"  dy={event.data[1]:+d}"
        self.liveCapture.emit(f"{event.kind}  {event.token}{char}{data}")

    def _append_live_capture(self, line: str) -> None:
        if (
            self.capture_list.count() == 1
            and self.capture_list.item(0).text() == "(listening…)"
        ):
            self.capture_list.clear()
        self.capture_list.addItem(line)
        self.capture_list.scrollToBottom()

    def _sync_ignored_region(self) -> None:
        """Clicks on this window are operating the editor, not the macro.

        pynput can filter by coordinates; evdev cannot, so under the preferred
        backend this is best-effort only.
        """
        frame = self.frameGeometry()
        self.app.recorder.ignore_click_region = (
            frame.x(),
            frame.y(),
            frame.width(),
            frame.height(),
        )

    def moveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.session.recording:
            self._sync_ignored_region()
        super().moveEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.session.recording:
            self._sync_ignored_region()
        super().resizeEvent(event)

    def _show_capture(self, outcome) -> None:
        """Lists what the last recording actually caught.

        A recording is authored blind: the pad has no screen and the window need
        not even be open. When what was captured is not what was done there was
        nothing to look at, so the only move was to guess -- and "it moved on
        its own" is not a thing anyone can debug from a status bar.
        """
        where = outcome.where or outcome.error or "nothing was captured"
        self.capture_title.setText(f"Key {outcome.key + 1} {outcome.gesture} - {where}")

        self.capture_list.clear()
        if outcome.dropped_secrets:
            self.capture_list.addItem(
                f"! {outcome.dropped_secrets} step(s) removed: looked like a password"
            )

        if outcome.error or not outcome.where:
            # Empty / rejected: show the capture log when we have one, else the
            # error (Wayland / input group hints live there).
            lines = (
                list(self.app.recorder.summary(self.session.last_steps))
                if self.session.last_steps
                else []
            )
            if lines:
                self.capture_list.addItems(lines)
            elif outcome.error:
                self.capture_list.addItem(outcome.error)
            else:
                self.capture_list.addItem("(nothing)")
            return

        # What will actually run, read back out of the profile -- not the raw
        # capture. The two are not the same list and saying so matters: fixed-
        # screen capture gains a home step, long text becomes one typed run,
        # and a long move becomes several.
        # Showing the capture alone meant the window described something the
        # pad was not going to do.
        action = self.app.profile.action(outcome.key, outcome.gesture)
        if action.kind == "sequence" and action.slot < len(self.app.profile.device_macros):
            lines = [step.describe() for step in self.app.profile.device_macros[action.slot]]
        else:
            lines = [action.describe()]

        self.capture_list.addItems(lines or ["(nothing)"])

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._closing:
            super().closeEvent(event)
            return
        self._closing = True
        self._connection_timer.stop()
        self.app.close()
        self._io_queue.put(None)
        thread, self._io_thread = self._io_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        super().closeEvent(event)


def run_gui(port: str = "") -> int:
    qt_app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(port=port)
    window.show()
    return qt_app.exec()
