"""The editor window.

The only module in the package that imports a UI toolkit, which is what
keeps every other module in the package runnable headless.
"""

from __future__ import annotations

import datetime
import logging
import os
import queue
import re
import sys
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
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
    Settings,
    binary,
    export_profile,
    is_factory_default,
    load_profile_file,
    profile_backup_paths,
)
from ..config.model import (
    EDITABLE_GESTURES,
    MAX_TEXT_SPEED_MS,
    MIN_TEXT_SPEED_MS,
    accel_sensitive_macro_slots,
    default_profile,
    macro_storage_usage,
    text_speed_shown,
    text_speed_stored,
)
from ..device import DeviceError, candidates
from ..i18n import LANGUAGES, active_language, language_name, set_language, tr
from ..runtime import resource_path
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

#: How long to wait before looking again for a window in which the keypad can
#: be reflashed. Flashing takes the pad away for a few seconds, so it never
#: interrupts a recording or a profile transfer -- and it has to come back and
#: ask, because a cable that stays put never produces a second connect to
#: notice it on.
FIRMWARE_UPDATE_RETRY_MS = 2000

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


class _EditorClickWatch(QObject):
    """Tells the recorder which clicks were aimed at the editor itself.

    Installed on the application, so it sees every press delivered anywhere in
    this process -- the window, its dialogs, its menus. That delivery is the
    whole signal: Qt hands a press to a widget here only when the click really
    did land on one, which is what the screen rectangle this replaces was trying
    to work out from coordinates and kept getting wrong. A click that went to
    the application in front is never seen here, so it stays in the recording
    where it belongs.

    One per application and owned by it, rather than one per window. An
    application-wide filter runs for every event in the process, so a filter
    installed per window costs a Python call per event per window ever made --
    which the tests, that build dozens, felt as the suite grinding to a halt.
    """

    def __init__(self, recorder, parent) -> None:
        super().__init__(parent)
        self.recorder = recorder

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.MouseButtonPress:
            self.recorder.note_editor_click()
        return False


def _watch_editor_clicks(recorder) -> None:
    """Points the one watcher at this window's recorder, making it if needed."""
    application = QApplication.instance()
    if application is None:
        return
    watch = application.findChild(_EditorClickWatch)
    if watch is None:
        watch = _EditorClickWatch(recorder, application)
        application.installEventFilter(watch)
    else:
        watch.recorder = recorder


class MainWindow(QMainWindow):
    # Worker threads emit these; Qt delivers them on the GUI thread.
    statusMessage = Signal(str)
    failed = Signal(str, str)
    connectionChanged = Signal()
    #: True while the pointer-acceleration question is on screen anywhere.
    #:
    #: Deliberately not per-window. What it protects is the one event loop the
    #: modal is nested inside, and a second window's copy of the same 900 ms
    #: startup timer firing in that loop opens a second identical dialog on top
    #: of the first -- which is a per-instance flag's blind spot exactly.
    _pointer_prompt_open = False

    #: A board macroKey knows is plugged in but is not answering the protocol.
    flashOffer = Signal(str)
    #: Flashing needs the board put into its bootloader by hand: id, and what
    #: to physically do.
    flashNeedsHelp = Signal(str, str)
    #: Firmware is on, so the pad can be connected to.
    flashSucceeded = Signal()
    recordingChanged = Signal()
    #: A newer app release was found, downloaded and put in place: its version
    #: and the release's own notes. The notes travel with it because an update
    #: that installs itself has to be able to say what it changed -- a version
    #: number alone leaves someone to guess why the app now behaves differently.
    appUpdateReady = Signal(str, str)
    #: The pad is behind: board id, the version it runs, the version on offer.
    firmwareUpdateFound = Signal(str, str, str)
    #: A firmware update finished: the version replaced and the version
    #: written, the latter empty if it turned out there was nothing to write.
    firmwareUpdateFinished = Signal(str, str)
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
        self._flashing = False
        #: Boards already offered a firmware install this session. Auto-connect
        #: retries on a timer, and a board with no firmware fails every one of
        #: them -- without this, declining the offer means being asked again a
        #: few seconds later, forever.
        self._flash_offered: set[str] = set()
        #: `board id@firmware version` already looked at this session. The pad
        #: is identified on every reconnect, and the answer cannot change while
        #: it keeps reporting the same version -- so asking the network again on
        #: each retry of a flapping cable would be all cost and no news.
        self._firmware_checked: set[str] = set()
        #: `(version, notes)` once a downloaded release is in place, so the
        #: restart notice is shown once rather than on every later check.
        self._app_update_staged: tuple[str, str] = ()
        #: The open non-modal question, if any. Nothing owns it otherwise.
        self._open_question = None
        #: Same, for a notice. Separate, so one cannot evict the other.
        self._open_notice = None
        self._syncing = False
        self._resetting = False
        self._profile_prompt_open = False
        self._capture_setup_running = False
        self._profiles_diverged = False
        # Set on connect: the pad is holding factory defaults while this
        # computer still has work. Changes what the mismatch dialog says and
        # which way it leans, because the two cases need opposite answers.
        self._device_lost_its_profile = False
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
            tr(
                "Hold any key on its own for 3 seconds to record into it - the pixel "
                "turns red. Hold the same key again to store what you did. "
                "After setup you can quit this app; the pad keeps working as a keyboard."
            )
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

        self.record_banner = QLabel(tr("  ● RECORDING - hold the same key again to finish  "))
        self.record_banner.setStyleSheet(
            "background: #c0392b; color: white; font-weight: 600; padding: 6px;"
        )
        self.record_banner.setAlignment(Qt.AlignCenter)
        self.record_banner.setVisible(False)
        layout.insertWidget(0, self.record_banner)

        self.storage_label = QLabel()
        self.storage_label.setToolTip(
            tr(
                "Shared keypad macro storage (keyboard + mouse steps).\n"
                "All 16 slots draw from the same 308-record pool."
            )
        )
        self.statusBar().showMessage(tr("Not connected"))
        self.statusBar().addPermanentWidget(self.storage_label)
        self.statusMessage.connect(self.statusBar().showMessage)
        self.failed.connect(self._show_error)
        self.connectionChanged.connect(self._refresh_connection)
        self.flashOffer.connect(self._offer_flash)
        self.flashNeedsHelp.connect(self._flash_needs_help)
        self.flashSucceeded.connect(self._flash_succeeded)
        self.appUpdateReady.connect(self._app_update_ready)
        self.firmwareUpdateFound.connect(self._firmware_update_found)
        self.firmwareUpdateFinished.connect(self._firmware_update_finished)
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

        # On the application, not on this window: a press that reaches a dialog
        # or a menu is still the editor being operated rather than the macro.
        # See `_EditorClickWatch`, and `Recorder._drop_editor_clicks` for what
        # it is evidence of.
        _watch_editor_clicks(self.app.recorder)

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
        # Last, and quietly. Nothing here is on the path to using the keypad,
        # and the check is a network round trip that must not be in front of a
        # window someone is waiting for.
        QTimer.singleShot(2500, self._start_update_checks)

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
        profile_menu = self.menuBar().addMenu(tr("Profile"))
        self._profile_menu = profile_menu
        import_action = QAction(tr("Import…"), self)
        import_action.triggered.connect(lambda: self._import_profile())
        export_action = QAction(tr("Export…"), self)
        export_action.triggered.connect(lambda: self._export_profile())
        restore_action = QAction(tr("Restore previous version…"), self)
        restore_action.triggered.connect(lambda: self._restore_profile_backup())
        profile_menu.addAction(import_action)
        profile_menu.addAction(export_action)
        profile_menu.addSeparator()
        profile_menu.addAction(restore_action)

        help_menu = self.menuBar().addMenu(tr("Help"))
        mouse_help = QAction(tr("Mouse macro accuracy"), self)
        mouse_help.triggered.connect(lambda: self._show_mouse_help())
        gesture_help = QAction(tr("Recording gestures"), self)
        gesture_help.triggered.connect(lambda: self._show_gesture_help())
        setup_help = QAction(tr("Recording setup"), self)
        self._setup_help_action = setup_help
        setup_help.triggered.connect(lambda: self._retry_capture_setup())
        check_updates = QAction(tr("Check for updates"), self)
        check_updates.triggered.connect(lambda: self._check_updates_now())
        auto_app = QAction(tr("Update the app automatically"), self)
        auto_app.setCheckable(True)
        auto_app.setChecked(bool(self.app.settings.auto_update_app))
        auto_app.toggled.connect(self._auto_update_app_toggled)
        auto_firmware = QAction(tr("Update keypad firmware automatically"), self)
        auto_firmware.setCheckable(True)
        auto_firmware.setChecked(bool(self.app.settings.auto_update_firmware))
        auto_firmware.toggled.connect(self._auto_update_firmware_toggled)

        help_menu.addAction(mouse_help)
        help_menu.addAction(gesture_help)
        help_menu.addSeparator()
        help_menu.addAction(setup_help)
        help_menu.addSeparator()
        help_menu.addAction(check_updates)
        help_menu.addAction(auto_app)
        help_menu.addAction(auto_firmware)
        about = QAction(tr("About macroKey"), self)
        about.triggered.connect(lambda: self._show_about())

        help_menu.addSeparator()
        help_menu.addMenu(self._build_language_menu())
        help_menu.addSeparator()
        help_menu.addAction(about)

    def _build_language_menu(self) -> QMenu:
        """The language picker, under Help because it is set once and forgotten.

        Every entry names its language in that language, so someone who has
        landed in the wrong one can still find their way out.
        """
        menu = QMenu(tr("Language"), self)
        group = QActionGroup(self)
        group.setExclusive(True)
        current = self.app.settings.language or "system"
        for code in LANGUAGES:
            label = language_name(code)
            if code == "system":
                # The only entry whose name is a description rather than a
                # language, so it is the only one worth translating.
                label = f"{tr('System default')} ({language_name(active_language())})"
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(code == current)
            action.triggered.connect(lambda _checked=False, c=code: self._set_language(c))
            group.addAction(action)
            menu.addAction(action)
        self._language_menu = menu
        return menu

    def _auto_update_app_toggled(self, checked: bool) -> None:
        self.app.settings.auto_update_app = checked
        self.app.settings.save()

    def _auto_update_firmware_toggled(self, checked: bool) -> None:
        self.app.settings.auto_update_firmware = checked
        self.app.settings.save()

    def _check_updates_now(self) -> None:
        """Help > Check for updates: both halves, and say so either way."""
        self._check_app_update(asked_for=True)
        # Asked for explicitly, so let it look again at a pad it has already
        # cleared this session.
        self._firmware_checked.clear()
        hello = getattr(self.app.device, "hello", None)
        if hello is None:
            self.statusMessage.emit(tr("Connect the keypad to check its firmware"))
            return
        self._in_background(lambda: self._check_firmware_update(hello, asked_for=True))

    def _set_language(self, code: str) -> None:
        """Stores the choice and says when it takes effect.

        Nothing is retranslated here. Every label, tooltip and pinned button
        width in this window was computed from the language it was built with,
        so switching in place would leave a half-translated window with clipped
        buttons -- the restart is the honest version of that.
        """
        if code == (self.app.settings.language or "system"):
            return
        self.app.settings.language = code
        self.app.settings.save()
        QMessageBox.information(
            self,
            tr("Restart required"),
            tr("The language changes the next time macroKey starts."),
        )

    def _import_profile(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            tr("Import macroKey profile"),
            "",
            tr("macroKey profiles (*.json);;All files (*)"),
        )
        if not path:
            return
        try:
            profile = load_profile_file(path)
            self._adopt_local_profile(profile, tr("Imported {path}").format(path=path))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.critical(self, tr("Import failed"), str(exc))

    def _export_profile(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self,
            tr("Export macroKey profile"),
            "macrokey-profile.json",
            tr("macroKey profiles (*.json);;All files (*)"),
        )
        if not path:
            return
        try:
            export_profile(self.app.profile, path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, tr("Export failed"), str(exc))
            return
        self.statusBar().showMessage(tr("Exported profile to {path}").format(path=path))

    def _backup_label(self, path) -> str:
        """When it was kept, and whether it has anything in it.

        The time alone is not enough to choose by: the copies made either side
        of a keypad losing its profile are seconds apart, and the only thing
        that distinguishes them is that one still has the macros.
        """
        stamp = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        try:
            profile = load_profile_file(path)
        except (OSError, ValueError, KeyError, TypeError):
            return tr("{stamp} — unreadable").format(stamp=stamp)
        if is_factory_default(profile):
            return tr("{stamp} — empty (factory defaults)").format(stamp=stamp)
        macros = sum(1 for macro in profile.device_macros if macro)
        return tr("{stamp} — {macros} recorded macros").format(stamp=stamp, macros=macros)

    def _restore_profile_backup(self) -> None:
        """Restores one of the kept copies, chosen by the person restoring.

        A single most-recent backup was not a recovery path for the case it
        exists for: an empty profile saved twice puts the empty one in the slot
        and the good one out of it, and the restore offers the empty one back.
        """
        paths = profile_backup_paths()
        if not paths:
            QMessageBox.information(
                self, tr("No previous version"), tr("No profile backup exists yet.")
            )
            return

        labels = [self._backup_label(path) for path in paths]
        chosen, accepted = QInputDialog.getItem(
            self,
            tr("Restore previous profile?"),
            tr(
                "Replace the current local profile with one of the kept copies? "
                "Newest first. The keypad is not changed until you resolve Sync…."
            ),
            labels,
            0,
            False,
        )
        if not accepted or chosen not in labels:
            return
        path = paths[labels.index(chosen)]
        try:
            profile = load_profile_file(path)
            self._adopt_local_profile(profile, tr("Restored the previous local profile"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.critical(self, tr("Restore failed"), str(exc))

    def _adopt_local_profile(self, profile, message: str) -> None:
        previous = self.app.profile
        self.app.profile = profile
        try:
            self.app.save()
        except OSError as exc:
            self.app.profile = previous
            QMessageBox.critical(self, tr("Could not save profile"), str(exc))
            return
        self._profiles_diverged = self.app.device.connected
        self._refresh_all()
        self._refresh_connection()
        suffix = tr(" — use Sync… to update the keypad") if self._profiles_diverged else ""
        self.statusBar().showMessage(message + suffix)

    def _show_mouse_help(self) -> None:
        QMessageBox.information(
            self,
            tr("Mouse macro accuracy"),
            tr(
                "Default mouse replay is relative: clicks happen at the current pointer, "
                "and movement starts there. This is the reliable choice.\n\n"
                "The keypad replays a recorded movement over the time it was recorded "
                "over, rather than as one jump, so pointer acceleration affects the "
                "replay the same way it affected your hand. Flat acceleration removes "
                "the variable altogether -- macroKey will offer that next.\n\n"
                "Fixed position is experimental. It homes to the top-left and depends "
                "on the same monitor layout, scaling, pointer speed/acceleration, window "
                "positions, and application state. Test fixed-position macros on a safe "
                "target before assigning them to destructive actions.\n\n"
                "Clicking the Windows taskbar is the trap worth naming. Windows 11 "
                "centres taskbar icons by default, so every icon moves whenever the "
                "number of open apps changes -- opening macroKey itself is enough to "
                "shift them. A macro aimed at one then lands on its neighbour. Set "
                "Settings > Personalisation > Taskbar > Taskbar behaviours > Taskbar "
                "alignment to Left and pin what you aim at: pinned icons keep their "
                "place and new windows are added to their right."
            ),
        )
        self._offer_flat_pointer(asked_for=True)

    def _show_about(self) -> None:
        """Who made this, and where the rest of it is.

        Rich text for one reason: the address is worth clicking. `exec` rather
        than the non-modal `_notify` because a person opened this from a menu
        and is waiting on it, which is the case a modal box is actually for.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(tr("About macroKey"))
        box.setTextFormat(Qt.RichText)
        box.setText(
            f"<b>macroKey v{__version__}</b>"
            f"<p>{tr('Created by maduinos')}</p>"
            f'<p><a href="{MADUINOS_URL}">{MADUINOS_URL}</a></p>'
        )
        box.setTextInteractionFlags(Qt.TextBrowserInteraction)
        box.exec()

    def _show_gesture_help(self) -> None:
        QMessageBox.information(
            self,
            tr("Recording gestures"),
            tr(
                "Tap slot: hold a key by itself for 3 seconds.\n\n"
                "Double slot: tap, then press and hold the same key within 250 ms; "
                "keep holding for 3 seconds.\n\n"
                "The pixel turns red while all keyboard input is being captured. Hold "
                "the same key again to save, or use Discard recording in this window."
            ),
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
            tr("Leave as Auto to use whichever board identifies itself as a keypad.")
        )
        self._rescan_ports()
        self.port_box.setCurrentText(port or self.app.settings.port or AUTO_PORT)

        self.connect_button = QPushButton(tr("Connect"))
        # A button elides its label rather than refuse to shrink, so a toolbar
        # that does not fit squeezes it silently. Pin it to the widest text it
        # will ever carry -- the row then demands its real width instead, and
        # the label stops changing size as the state changes.
        # Sized against the translated labels, not the English ones: a button
        # pinned to the width of "Connect" elides "연결 해제".
        self.connect_button.setMinimumWidth(
            _button_width(
                self.connect_button,
                tr("Connecting..."),
                tr("Disconnect"),
                tr("Connect"),
            )
        )
        self.connect_button.clicked.connect(self._toggle_connection)

        self.link_label = QLabel()
        port_label = QLabel(tr("Port"))
        port_label.setBuddy(self.port_box)
        connection_row.addWidget(port_label)
        connection_row.addWidget(self.port_box)
        connection_row.addWidget(self.connect_button)
        connection_row.addWidget(self.link_label)

        self.sync_button = QPushButton(tr("Sync…"))
        # Same hazard as Connect: the label swaps to "Sync needed" at runtime.
        # That one is a state, not an action, so it has no ellipsis; the base
        # label keeps its own, because the dialog it opens asks which side wins.
        self.sync_button.setMinimumWidth(
            _button_width(self.sync_button, tr("Sync…"), tr("Sync needed"))
        )
        self.sync_button.setToolTip(
            tr("Resolve which profile wins when this computer and the keypad differ.")
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
            tr(
                "Pause between characters when the pad replays typed text.\n"
                "5 ms is what the pad does out of the box. Drop it to 1 ms for the\n"
                "fastest replay, or raise it if the receiving window misses the start."
            )
        )
        self.text_speed.valueChanged.connect(self._text_speed_changed)

        # Destructive, and the only control here that is, so it sits apart from
        # the knobs. No ellipsis: that marks a control that needs more input
        # before it can act, and this one only raises a yes/no confirmation.
        self.reset_button = QPushButton(tr("Reset"))
        self.reset_button.setMinimumWidth(_button_width(self.reset_button, tr("Reset")))
        self.reset_button.setToolTip(
            tr(
                "Clears every binding and every recorded macro -- on this computer\n"
                "and on the keypad -- and puts the hyper + 1..8 defaults back."
            )
        )
        self.reset_button.clicked.connect(self._reset_everything)

        brightness_label = QLabel(tr("Brightness"))
        brightness_label.setBuddy(self.brightness)
        controls_row.addWidget(brightness_label)
        controls_row.addWidget(self.brightness)
        controls_row.addWidget(self.brightness_value)
        controls_row.addSpacing(12)
        typing_label = QLabel(tr("Typing"))
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
        self.statusMessage.emit(
            tr("Typing speed {value} ms per character (saving…)").format(value=value)
        )
        self._text_speed_timer.start()

    def _text_speed_settled(self) -> None:
        self._apply(
            tr("Typing speed {value} ms per character").format(
                value=text_speed_shown(self.app.profile.text_speed_ms)
            )
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
        self.capture_title = QLabel(tr("Recording"))
        self.capture_title.setStyleSheet("font-weight: 600;")
        self.capture_setup_button = QPushButton(tr("Setup"))
        self.capture_setup_button.setToolTip(
            tr("Check or retry permission setup for global keyboard and mouse recording.")
        )
        self.capture_setup_button.clicked.connect(self._retry_capture_setup)
        self.capture_mouse = QCheckBox(tr("Include mouse"))
        self.capture_mouse.setChecked(bool(self.app.settings.recorder_capture_mouse))
        self.capture_mouse.setToolTip(
            tr(
                "Clicks, wheel, and pointer movement. By default clicks happen at "
                "the current pointer and movement is relative to it."
            )
        )
        self.capture_mouse.toggled.connect(self._mouse_capture_toggled)

        self.anchor_mouse = QCheckBox(tr("Fixed screen"))
        self.anchor_mouse.setChecked(bool(self.app.settings.recorder_anchor_mouse))
        self.anchor_mouse.setEnabled(self.capture_mouse.isChecked())
        self.anchor_mouse.setToolTip(
            tr(
                "Homes the pointer before recording and replay. Fixed clicks are only "
                "repeatable with the same monitor layout, scaling, pointer speed, and "
                "window positions. Relative/current-pointer replay is safer."
            )
        )
        self.anchor_mouse.toggled.connect(self._anchor_mouse_toggled)

        self.preserve_key_timing = QCheckBox(tr("Real timing"))
        self.preserve_key_timing.setChecked(
            bool(self.app.settings.recorder_preserve_key_timing)
        )
        self.preserve_key_timing.setToolTip(
            tr(
                "Record how long each key stays down (press, wait, release) instead of\n"
                "turning every keystroke into a tap. Use this for game holds like\n"
                "Shift+W. Ordinary shortcuts and typing macros usually leave this off.\n"
                "Long holds use several records (delays max out at 2550 ms each)."
            )
        )
        self.preserve_key_timing.toggled.connect(self._preserve_key_timing_toggled)

        self.cancel_recording_button = QPushButton(tr("Discard recording"))
        self.cancel_recording_button.setToolTip(
            tr("Stop global capture and discard everything recorded this time.")
        )
        self.cancel_recording_button.clicked.connect(self._cancel_recording)
        self.cancel_recording_button.setVisible(False)
        row.addWidget(self.capture_title)
        row.addWidget(self.capture_setup_button)
        row.addStretch(1)
        row.addWidget(self.capture_mouse)
        row.addWidget(self.anchor_mouse)
        row.addWidget(self.preserve_key_timing)
        row.addWidget(self.cancel_recording_button)

        self.capture_list = QListWidget()
        self.capture_list.setMaximumHeight(140)
        self.capture_list.setStyleSheet("font-family: monospace;")
        self.capture_list.addItem(
            tr("Hold a pad key for 3 seconds to record. Captured steps appear here.")
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

    def _offer_flat_pointer(self, *, asked_for: bool = False, because: str = "") -> None:
        """Offers to remove pointer acceleration from mouse replay.

        The pad sends relative movement, so the desktop decides how far that is
        on screen. An adaptive profile makes that decision depend on speed; the
        firmware replays at the recorded speed so the curve mostly cancels, but
        flat removes the variable and makes replay exact.

        It is someone's desktop preference, so this asks and never assumes, and
        a no is remembered. Yes is the default button: the dialog only appears
        because the user is recording mouse movement, flat is the better setting
        for that, and the text says both what else it changes and how to put it
        back -- so the recommended answer should not be the one that takes an
        extra keystroke. `asked_for` is the path from the Help menu, which both
        ignores that no and says something when there is nothing to fix.
        """
        if MainWindow._pointer_prompt_open:
            # A modal runs its own event loop, and this one is raised by a
            # 900 ms startup timer. Anything that fires inside that loop --
            # another window's copy of the same timer, this one rearmed -- gets
            # to open a second dialog on top of the first, and a third inside
            # that. It recurses until the stack gives out, and nothing on screen
            # explains why: the dialogs are identical.
            return
        profile = capture_setup.pointer_accel_profile()
        if profile is None:
            if asked_for:
                QMessageBox.information(
                    self,
                    tr("Pointer acceleration"),
                    tr(
                        "This desktop does not expose a pointer acceleration setting "
                        "that macroKey can read, so there is nothing to change here."
                    ),
                )
            return
        if profile == "flat":
            if asked_for:
                QMessageBox.information(
                    self,
                    tr("Pointer acceleration"),
                    tr(
                        "Pointer acceleration is already flat, which is the setting "
                        "mouse macros replay most accurately under."
                    ),
                )
            return
        if not asked_for and self.app.settings.pointer_accel_declined:
            return

        MainWindow._pointer_prompt_open = True
        self._ask(
            title=tr("Pointer acceleration"),
            text=because + tr(
                "Your desktop scales pointer movement by how fast it is "
                "(acceleration profile: {profile}).\n\n"
                "The keypad replays a movement at the speed it was recorded, "
                "which cancels most of that. It does not cancel all of it: a "
                "fast movement replays at the limit of what USB carries, so any "
                "delay stretches the gesture and the curve then multiplies it by "
                "less, which can put the macro short of where you drew it. "
                "Turning acceleration off removes the variable entirely.\n\n"
                "Switch to flat pointer acceleration? It changes how the mouse "
                "feels everywhere, not just in macros. To undo it later:\n\n"
                "{undo}"
            ).format(profile=profile, undo=capture_setup.pointer_accel_undo_hint()),
            buttons=QMessageBox.Yes | QMessageBox.No,
            default=QMessageBox.Yes,
            accepted=QMessageBox.Yes,
            then=self._make_pointer_flat,
            otherwise=lambda: self._pointer_fix_declined(asked_for=asked_for),
            always=self._pointer_prompt_closed,
        )

    def _offer_flat_pointer_for_fast_macro(self) -> None:
        """A recording just landed that this desktop's acceleration will move.

        The startup question is asked before there is anything to point at, and
        a no there is kept for good. This is the one moment the answer stops
        being abstract: a macro now exists whose replay cannot be paced finely
        enough for the curve to cancel, so it lands short of where it was drawn.
        Offered once, saying what changed, and then never again -- Help > Mouse
        macro accuracy is still there for anyone who wants it later.
        """
        settings = self.app.settings
        if self._closing or settings.pointer_accel_evidence_shown:
            return
        # Not declined yet means the startup offer still speaks for itself, and
        # spending the follow-up here would waste it on someone already asked.
        if not settings.pointer_accel_declined:
            return
        if not capture_setup.pointer_accel_can_be_flattened():
            return
        if not accel_sensitive_macro_slots(self.app.profile.device_macros):
            return
        settings.pointer_accel_evidence_shown = True
        settings.save()
        self._offer_flat_pointer(
            asked_for=True,
            because=tr(
                "The recording you just made moves the pointer faster than the "
                "keypad can replay a count at a time, so this desktop's "
                "acceleration has a say in how far it goes.\n\n"
            ),
        )

    @staticmethod
    def _pointer_prompt_closed() -> None:
        MainWindow._pointer_prompt_open = False

    def _make_pointer_flat(self) -> None:
        ok, message = capture_setup.set_pointer_accel_flat()
        self.statusBar().showMessage(message)
        if not ok:
            QMessageBox.warning(self, "Pointer acceleration", message)

    def _pointer_fix_declined(self, *, asked_for: bool) -> None:
        if asked_for:
            return
        self.app.settings.pointer_accel_declined = True
        self.app.settings.save()
        self.statusBar().showMessage(
            tr(
                "Left pointer acceleration alone. Help > Mouse macro accuracy "
                "offers it again."
            )
        )

    def _anchor_mouse_toggled(self, checked: bool) -> None:
        self.app.settings.recorder_anchor_mouse = checked
        self.app.recorder.anchor_mouse = checked
        self.app.settings.save()

    def _preserve_key_timing_toggled(self, checked: bool) -> None:
        self.app.settings.recorder_preserve_key_timing = checked
        self.app.recorder.preserve_key_timing = checked
        self.app.settings.save()

    def _cancel_recording(self) -> None:
        if self.session.recording:
            self.session.abort(tr("Recording discarded"))

    def _build_keys(self) -> QWidget:
        """The eight keys, and nothing wrapped around them.

        This used to be a tab per layer, then a single tab, then this: eight
        rows and two columns, which is the whole pad.
        """
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        grid = QGridLayout(page)
        grid.setContentsMargins(8, 8, 8, 8)

        headers = (tr("Key"), *(tr(g.title()) for g in EDITABLE_GESTURES))
        for column, title in enumerate(headers):
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
        grid.addWidget(QLabel(tr("LED")), KEY_COUNT + 1, 0)
        swatch = QPushButton()
        swatch.setToolTip(tr("Colour the pad rests at when nothing is happening."))
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
        dialog.setWindowTitle(tr("Resting LED colour"))
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
        self._apply(tr("Resting LED #{value}").format(value=value))
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
                self.statusMessage.emit(
                    tr("Preview unavailable: {detail}").format(detail=exc)
                )

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
                self.statusMessage.emit(
                    tr("Preview stopped: {detail}").format(detail=exc)
                )

        self._in_background(worker)

    def _refresh_swatch(self) -> None:
        value = self.app.profile.resting_color
        colour = QColor(f"#{value}")
        # Black reads as "off" rather than as a colour, so say so: an empty
        # black rectangle looks like a rendering failure.
        self.swatch.setText(tr("off") if colour.value() == 0 else f"#{value}")
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
            self.app.profile.device_macros,
            capacity=self.app.profile_layout.record_capacity,
        )
        self.storage_label.setText(
            tr("Storage {used_pct}% used · {free_pct}% free ({used}/{capacity})").format(
                used_pct=used_pct, free_pct=free_pct, used=used, capacity=capacity
            )
        )

    def _edit(self, key: int, gesture: str) -> None:
        dialog = SlotDialog(self, self.app, key, gesture)
        accepted = dialog.exec() == QDialog.Accepted
        # The recording checkboxes used to be read back here: the slot dialog
        # carried its own copies of them, and closing it left this row showing
        # the old values. They are only in this window now, so there is nothing
        # to reconcile -- but the pad may well have connected or dropped while a
        # modal dialog was over it, and that still has to be drawn.
        self._refresh_connection()
        if not accepted:
            return
        if dialog.repeat_applied:
            # The repeat row wrote the binding itself: it may have had to place
            # a macro, and which slot is free is the app's business, not a
            # dialog's. Everything after the write is the same as any edit.
            self.app.profile.reclaim_storage()
            self._refresh_all()
            self._apply(
                tr("Key {key} {gesture}").format(key=key + 1, gesture=tr(gesture))
            )
            return
        if dialog.result_action is not None:
            self.app.profile.set_action(key, gesture, dialog.result_action)
            self.app.profile.reclaim_storage()
            self._refresh_all()
            self._apply(
                tr("Key {key} {gesture}").format(key=key + 1, gesture=tr(gesture))
            )

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
        box.setWindowTitle(tr("Reset everything?"))
        box.setText(
            tr(
                "Every key binding and every recorded macro is cleared -- on this "
                "computer and on the keypad -- and the hyper + 1..8 defaults go "
                "back.\n\nThe previous local profile remains available under "
                "Profile > Restore previous version."
            )
        )
        reset = box.addButton(tr("Reset"), QMessageBox.DestructiveRole)
        cancel = box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is not reset:
            self.statusBar().showMessage(tr("Reset cancelled"))
            return
        if self.session.recording:
            self.statusBar().showMessage(tr("Finish or discard the recording before resetting"))
            return

        connected = self.app.device.connected
        self._resetting = True
        self._refresh_connection()
        if connected:
            self.statusMessage.emit(tr("Resetting the keypad…"))

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
            self.statusMessage.emit(
                tr("The keypad was cleared, but could not save: {detail}").format(detail=exc)
            )
            self._refresh_connection()
            return

        self._profiles_diverged = False
        if device_was_reset and self.app.device.connected:
            self._profiles_diverged = False
            self.statusBar().showMessage(
                tr("Reset. The keypad and this computer are back to defaults.")
            )
        else:
            self.statusBar().showMessage(
                tr(
                    "Reset this computer. The keypad still holds its own bindings "
                    "until you connect and choose Push in Sync…."
                )
            )
        self._refresh_connection()

    def _reset_failed(self, message: str) -> None:
        self._resetting = False
        self._refresh_connection()
        QMessageBox.critical(
            self,
            tr("Reset failed"),
            tr("The keypad kept its profile: {message}").format(message=message),
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
            self.statusMessage.emit(tr("Could not save: {detail}").format(detail=exc))
            return
        if not self.app.device.connected:
            self.statusMessage.emit(
                tr("{what} saved. It reaches the keypad on the next connect.").format(what=what)
            )
            return
        if (
            self._connecting
            or self._syncing
            or self._resetting
            or self.session.recording
        ):
            self.statusMessage.emit(
                tr(
                    "{what} saved locally. The current device operation will finish first."
                ).format(what=what)
            )
            return
        if self._profiles_diverged:
            self.statusMessage.emit(
                tr(
                    "{what} saved locally. Profiles still differ; use Sync… to choose a side."
                ).format(what=what)
            )
            return
        try:
            blob = binary.encode_profile(
                self.app.profile, profile_size=self.app.profile_layout.size
            )
        except ValueError as exc:
            self.statusMessage.emit(
                tr("Could not build the keypad profile: {detail}").format(detail=exc)
            )
            return

        def worker() -> None:
            try:
                self.app.device.write_profile(blob)
                self.app.confirm_on_device()
            except (DeviceError, ValueError, OSError) as exc:
                self.statusMessage.emit(
                    tr("{what} saved, but the device write failed: {detail}").format(
                        what=what, detail=exc
                    )
                )
                return
            self.statusMessage.emit(
                tr("Done - {what} written to the keypad").format(what=what)
            )

        self.statusMessage.emit(
            tr("{what} saved; writing to the keypad…").format(what=what)
        )
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
        self._apply(
            tr("Brightness {value}").format(value=self.app.profile.brightness)
        )

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
        if self._connecting or self._syncing or self._resetting or self._flashing:
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
                matches, device_is_empty = self.app.compare_with_device()
                profiles_differ = not matches
                self._profiles_diverged = profiles_differ
                # Two ways to learn the pad lost its profile. The flag is the
                # firmware saying so outright (0.9.1 and later); the comparison
                # is the inference that works on older firmware, and on a pad
                # that was cleared by something other than a failed read.
                hello = getattr(self.app.device, "hello", None)
                self._device_lost_its_profile = bool(
                    getattr(hello, "profile_was_reset", False)
                ) or (device_is_empty and not is_factory_default(self.app.profile))
                if self._device_lost_its_profile:
                    log.warning(
                        "keypad is holding factory defaults (firmware reported "
                        "reset=%s); this computer still has bindings",
                        getattr(hello, "profile_was_reset", False),
                    )
                if not profiles_differ:
                    self.statusMessage.emit(tr("Connected"))
                # Now that the pad has said which board and which firmware it
                # is. Still on the worker thread, so a slow network delays
                # nothing anyone is looking at.
                self._check_firmware_update(hello)
            except DeviceError as exc:
                # A board this app knows, that will not answer the protocol, is
                # almost always a board with no macroKey firmware on it -- the
                # state every newly built keypad starts in. Offering to flash it
                # is the whole "solder it, plug it in, use it" path, so it comes
                # before reporting a failure the person can do nothing with.
                attached = self._flashable_board()
                if attached is not None:
                    self.flashOffer.emit(attached.board.id)
                elif quiet:
                    self.statusMessage.emit(
                        tr("No keypad found: {detail}").format(
                            detail=exc.args[0].splitlines()[0]
                        )
                    )
                else:
                    self.failed.emit(tr("Connect failed"), str(exc))
            except (ValueError, OSError) as exc:
                self.statusMessage.emit(
                    tr("Could not update the keypad: {detail}").format(detail=exc)
                )
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

    # ------------------------------------------------------------- questions --

    def _ask(
        self, *, title, text, buttons, default, accepted, then, otherwise=None, always=None
    ) -> None:
        """A question that does not stop the event loop while it is open.

        `QMessageBox.question` runs its own loop until answered. That is fine
        for a dialog a click asked for, but these are raised by a connect that
        happened on its own -- and a modal loop entered from a background event
        stops everything else the window was doing, including the reconnect
        that would make the question moot. It also makes a headless run hang
        forever on a dialog nobody can answer.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        box.setDefaultButton(default)
        box.setAttribute(Qt.WA_DeleteOnClose)

        def finished(result: int) -> None:
            self._open_question = None
            # `always` runs even on the way out, because what it releases is
            # usually a guard against asking twice -- and a guard that a close
            # can leave set is a question that never gets asked again.
            if always is not None:
                always()
            if self._closing:
                return
            if result == accepted:
                then()
            elif otherwise is not None:
                otherwise()

        box.finished.connect(finished)
        # Held so it is not collected while it is on screen.
        self._open_question = box
        box.open()

    # ---------------------------------------------------------------- flash --

    def _flashable_board(self):
        """A recognised board attached that is not currently talking to us.

        Called from the connect worker, so it must not touch a widget.
        """
        from .. import flash

        try:
            return flash.find_board()
        except Exception:  # noqa: BLE001 - offering to flash must never break connecting
            log.exception("could not look for a flashable board")
            return None

    def _offer_flash(self, board_id: str) -> None:
        """Asks once, then does the rest by itself.

        This is a modal on an auto-connect, which errors deliberately are not:
        an offer is actionable and is the entire point of plugging a freshly
        built keypad in, where "no keypad found" would be a dead end. It is
        asked once per board per session either way.

        `_closing` is not a formality. The offer is emitted from the connect
        worker and delivered later on the GUI thread, which can be after the
        window has been told to close -- and putting a modal dialog on a widget
        that is being destroyed aborts the process.
        """
        from ..boards import board_by_id
        from ..flash import NoImage, find_image

        if self._closing or self._connecting or self._flashing:
            return
        if board_id in self._flash_offered:
            return
        self._flash_offered.add(board_id)
        board = board_by_id(board_id)
        if board is None:
            return
        try:
            find_image(board)
        except NoImage:
            # A source checkout with no images built. Say so rather than
            # offering something that cannot be delivered.
            self.statusMessage.emit(
                tr("{board} found, but this build has no firmware for it").format(
                    board=board.display_name
                )
            )
            return

        self._ask(
            title=tr("Install firmware?"),
            text=tr(
                "{board} is plugged in but is not running macroKey firmware.\n\n"
                "Install it now? The keypad will restart and be ready to use."
            ).format(board=board.display_name),
            buttons=QMessageBox.Yes | QMessageBox.No,
            default=QMessageBox.Yes,
            accepted=QMessageBox.Yes,
            then=lambda: self._flash_board(board_id),
        )

    def _flash_board(self, board_id: str) -> None:
        from ..boards import board_by_id
        from ..flash import FlashError, NeedsManualBootloader, flash

        board = board_by_id(board_id)
        if board is None:
            return
        self._flashing = True
        self._refresh_connection()

        def worker() -> None:
            try:
                flash(board_id=board_id, status=self.statusMessage.emit)
            except NeedsManualBootloader as exc:
                self.flashNeedsHelp.emit(board_id, exc.hint)
                return
            except FlashError as exc:
                self.failed.emit(tr("Firmware install failed"), str(exc))
                return
            except RuntimeError:
                return  # window closed mid-flash
            finally:
                self._flashing = False
                try:
                    self.connectionChanged.emit()
                except RuntimeError:
                    pass
            self.flashSucceeded.emit()

        self._in_background(worker)

    def _flash_needs_help(self, board_id: str, hint: str) -> None:
        """The one place a person has to act, and the only place we ask.

        A board that has never run macroKey firmware has nothing listening for a
        request to reboot into its bootloader, so this cannot be automated away.
        What it can be is watched: say what to do, and start flashing the moment
        the bootloader appears rather than making anyone race a command.
        """
        from ..boards import board_by_id

        board = board_by_id(board_id)
        if board is None or self._closing:
            return
        self._ask(
            title=tr("One step by hand"),
            text=tr(
                "{board} has never run macroKey firmware, so it has to be put "
                "into its bootloader by hand:\n\n{hint}\n\n"
                "Do that now, then press OK -- the firmware is installed as soon "
                "as the board appears."
            ).format(board=board.display_name, hint=hint),
            buttons=QMessageBox.Ok | QMessageBox.Cancel,
            default=QMessageBox.Ok,
            accepted=QMessageBox.Ok,
            then=lambda: self._flash_board(board_id),
        )

    def _flash_succeeded(self) -> None:
        if self._closing:
            return
        # It has firmware now, so a later unplug-and-replug is a fresh question.
        self._flash_offered.clear()
        self.statusMessage.emit(tr("Firmware installed"))
        self._toggle_connection(quiet=True)

    # -------------------------------------------------------------- updates --
    #
    # Two updates, one rule: the firmware follows the app. The app carries an
    # image for every board, so bringing a pad level needs no network at all --
    # the network is what brings a newer *app*, and the firmware inside it comes
    # with it. The two versions that have to agree about the profile layout then
    # move together instead of separately.

    def _start_update_checks(self) -> None:
        """Startup: sweep away the last update, then look for the next one."""
        from ..update import selfupdate

        selfupdate.cleanup()
        if self.app.settings.auto_update_app:
            self._check_app_update()

    def _check_app_update(self, *, asked_for: bool = False) -> None:
        """Looks for a newer release and, when there is one, puts it in place.

        `asked_for` distinguishes the menu item from the startup check: the
        automatic one says nothing when there is nothing to say, because a
        status bar that announces "up to date" on every launch is noise that
        trains people to stop reading it.
        """
        from ..update import UpdateError, selfupdate

        usable, why = selfupdate.supported()
        if not usable:
            if asked_for:
                self.statusMessage.emit(
                    tr("No app update from here: {detail}").format(detail=why)
                )
            return
        if self._app_update_staged:
            self.appUpdateReady.emit(*self._app_update_staged)
            return

        def worker() -> None:
            try:
                release = selfupdate.check()
                if release is None:
                    if asked_for:
                        self.statusMessage.emit(
                            tr("macroKey v{version} is the newest release").format(
                                version=__version__
                            )
                        )
                    return
                selfupdate.apply(release, status=self.statusMessage.emit)
            except UpdateError as exc:
                # Never a dialog. The app works perfectly without this.
                log.info("app update: %s", exc)
                if asked_for:
                    self.statusMessage.emit(
                        tr("Could not update the app: {detail}").format(detail=exc)
                    )
                return
            except RuntimeError:
                return  # window closed mid-download
            self.appUpdateReady.emit(release.version, release.notes)

        self._in_background(worker)

    def _app_update_ready(self, version: str, notes: str = "") -> None:
        """The new binary is in place; only a restart can start running it.

        The release's own notes go in the box. An update nobody asked for and
        nobody watched happen has to say what it did: a version number alone
        leaves someone to work out on their own why the app they did not choose
        to change now behaves differently.
        """
        if self._closing:
            return
        self._app_update_staged = (version, notes)
        self.statusMessage.emit(
            tr("macroKey v{version} installed - restart to use it").format(version=version)
        )
        text = tr(
            "macroKey v{version} has been downloaded and installed.\n\n"
            "Close and reopen the app to start using it. The keypad keeps "
            "working as a keyboard either way."
        ).format(version=version)
        summary = _release_summary(notes)
        if summary:
            text = f"{text}\n\n{tr('What changed:')}\n\n{summary}"
        self._notify(tr("Update installed"), text)

    def _check_firmware_update(self, hello, *, asked_for: bool = False) -> None:
        """Is the pad behind? Runs on the connect worker, never on the GUI.

        Asked once per board and firmware version per session. The pad is
        identified again on every reconnect, and the answer cannot change while
        it keeps reporting the same version -- so a flapping cable would
        otherwise ask the network the same question every few seconds.
        """
        from ..boards import board_by_id
        from ..update import UpdateError, firmware

        if hello is None:
            return
        if not self.app.settings.auto_update_firmware and not asked_for:
            return
        board = board_by_id(getattr(hello, "board", "") or "")
        running = getattr(hello, "firmware", "") or ""
        if board is None or not running:
            return
        key = f"{board.id}@{running}"
        if key in self._firmware_checked:
            return
        self._firmware_checked.add(key)
        try:
            candidate = firmware.best(board, running, status=self.statusMessage.emit)
        except UpdateError as exc:
            log.info("firmware update: %s", exc)
            return
        except Exception:  # noqa: BLE001 - a bad update check must not break connecting
            log.exception("could not look for a firmware update")
            return
        if candidate is None or candidate.version is None:
            return
        self.firmwareUpdateFound.emit(board.id, running, candidate.version)

    def _firmware_update_found(self, board_id: str, running: str, version: str) -> None:
        """Writes it, unattended -- but never on top of something in progress.

        Flashing takes the keypad away for a few seconds, so it waits for a
        window that is not recording, syncing, or resetting, and looks again in
        a moment rather than giving up. "Try again on the next connect" was not
        a plan: a cable that stays put produces exactly one connect, and this
        arrives *during* it -- the check runs on the connect worker, so
        `_connecting` is still true when the answer is delivered. Dropping it
        there meant the update never happened at all on a link that worked.
        """
        if self._closing:
            return
        if not self.app.device.connected:
            # The pad went away while this was in flight. Forget it, so the
            # connect that brings it back asks about it again.
            self._firmware_checked.discard(f"{board_id}@{running}")
            return
        if (
            self._flashing
            or self._connecting
            or self._syncing
            or self._resetting
            or self.session.recording
            # A question on screen is waiting for an answer about this keypad --
            # "which profile wins" above all. Taking the pad away underneath it
            # would leave the answer to apply to a device that is not there.
            or self._open_question is not None
            or self._profile_prompt_open
        ):
            QTimer.singleShot(
                FIRMWARE_UPDATE_RETRY_MS,
                lambda: self._firmware_update_found(board_id, running, version),
            )
            return
        if self.app.settings.auto_update_firmware:
            self._update_firmware(board_id, running, version)
            return
        # Automatic updates are off, so this can only have come from Help >
        # Check for updates. Found is not the same as wanted.
        self._ask(
            title=tr("Update the keypad?"),
            text=tr(
                "The keypad is running firmware {old}; this app has {new}.\n\n"
                "Update it now? It takes a few seconds and the keypad restarts."
            ).format(old=running, new=version),
            buttons=QMessageBox.Yes | QMessageBox.No,
            default=QMessageBox.Yes,
            accepted=QMessageBox.Yes,
            then=lambda: self._update_firmware(board_id, running, version),
        )

    def _update_firmware(self, board_id: str, running: str, version: str) -> None:
        from ..flash import FlashError, NeedsManualBootloader
        from ..update import firmware

        self._flashing = True
        self.statusMessage.emit(
            tr("Updating keypad firmware {old} to {new}").format(old=running, new=version)
        )
        # The flasher reboots the board through the port this app is holding
        # open, so let go of it first. `_reconnect_allowed` goes with it: the
        # link is about to drop because we asked, not because a cable moved.
        self._reconnect_allowed = False
        self._reconnect_at = None
        self.app.disconnect()
        self._refresh_connection()

        def worker() -> None:
            installed = None
            try:
                installed = firmware.update(board_id, running, status=self.statusMessage.emit)
            except NeedsManualBootloader as exc:
                self.flashNeedsHelp.emit(board_id, exc.hint)
                return
            except FlashError as exc:
                self.failed.emit(tr("Firmware update failed"), str(exc))
                return
            except RuntimeError:
                return  # window closed mid-flash
            finally:
                self._flashing = False
                try:
                    self.connectionChanged.emit()
                except RuntimeError:
                    pass
            # `update` decides again what to write, and can decide there is
            # nothing -- the image went away, or a second look says the pad is
            # current. The link still has to come back either way, but saying
            # "installed" about a write that did not happen is a lie the status
            # bar has no way to take back.
            self.firmwareUpdateFinished.emit(running, installed or "")

        self._in_background(worker)

    def _firmware_update_finished(self, previous: str, version: str) -> None:
        """Says the pad was rewritten, rather than only mentioning it.

        This one happens with nobody watching -- it is triggered by plugging a
        cable in, takes the keypad away for a few seconds, and used to leave a
        single status bar line that scrolls past. A pad that has been reflashed
        behind someone's back and then behaves differently is a bug report
        waiting to happen, so it gets a box and both version numbers.
        """
        if self._closing:
            return
        self._flash_offered.clear()
        if not version:
            self.statusMessage.emit(tr("Keypad firmware was already current"))
            self._toggle_connection(quiet=True)
            return
        self.statusMessage.emit(
            tr("Keypad firmware updated to {version}").format(version=version)
        )
        self._notify(
            tr("Keypad firmware updated"),
            tr(
                "The keypad was running firmware {old} and this build carries "
                "{new}, so it was written to the pad. Your macros were not "
                "touched.\n\n"
                "Firmware comes with the app and follows it, so this happens "
                "whenever the app moves ahead. What changed is in the app's "
                "release notes."
            ).format(old=previous or tr("an older version"), new=version),
        )
        self._toggle_connection(quiet=True)

    def _notify(self, title: str, text: str) -> None:
        """An information box that does not stop the event loop while it is up.

        Same reason as `_ask`: this is raised by something that happened on its
        own, and a modal loop entered from a background event stops everything
        else the window was doing.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.Ok)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.finished.connect(lambda _result: setattr(self, "_open_notice", None))
        # Its own handle rather than `_open_question`: that one is what keeps a
        # *question* alive while it is on screen, and putting a notice in it
        # would drop the question's only reference the moment one appeared.
        self._open_notice = box
        box.open()

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
        if self._device_lost_its_profile:
            # Not a disagreement: the pad is empty and this computer is not.
            # Offering "Pull" first here is offering to delete the macros, and
            # it is one keystroke on a dialog that looks like the usual one.
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(tr("The keypad lost its profile"))
            box.setText(
                tr(
                    "The keypad is holding factory defaults -- no recorded macros "
                    "and the plain hyper + 1..8 bindings. This computer still has "
                    "yours.\n\n"
                    "Push: put this computer's profile back on the keypad.\n"
                    "Pull: accept the empty one, losing what is on this computer.\n"
                    "Cancel: leave both as they are.\n\n"
                    "Profile > Restore previous version keeps earlier copies if "
                    "this computer's profile is already the empty one."
                )
            )
        else:
            box.setWindowTitle(tr("Profile differs"))
            box.setText(
                tr(
                    "This computer and the keypad have different profiles.\n\n"
                    "Pull: use what is on the keypad.\n"
                    "Push: overwrite the keypad with this computer's profile.\n"
                    "Cancel: leave both as they are."
                )
            )
        pull = box.addButton(tr("Pull from keypad"), QMessageBox.AcceptRole)
        push = box.addButton(tr("Push to keypad"), QMessageBox.DestructiveRole)
        cancel = box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        # Leaning is the whole point of telling the two cases apart: recovering
        # the pad is the safe answer when it is the pad that forgot.
        box.setDefaultButton(push if self._device_lost_its_profile else cancel)
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
                tr(
                    "Connected — profiles still differ. Local edits will not overwrite "
                    "the keypad until Sync… is resolved."
                )
            )
            return
        if self.session.recording:
            self.statusBar().showMessage(
                tr("Finish or discard the recording before synchronizing profiles")
            )
            return

        self._syncing = True
        self._refresh_connection()
        if clicked is pull:
            self.statusMessage.emit(tr("Reading the keypad profile…"))

            def worker() -> None:
                try:
                    profile = self.app.pull_profile()
                except (DeviceError, ValueError, OSError) as exc:
                    self.syncFailed.emit(str(exc))
                    return
                self.profileAdopted.emit(profile)

        else:
            self.statusMessage.emit(tr("Writing this computer's profile to the keypad…"))

            def worker() -> None:
                try:
                    self.app.push_profile()
                except (DeviceError, ValueError, OSError) as exc:
                    self.syncFailed.emit(str(exc))
                    return
                self.syncSucceeded.emit(tr("Keypad updated from this computer"))

        self._in_background(worker)

    def _adopt_profile(self, profile) -> None:
        self.app.profile = profile
        try:
            self.app.save()
        except OSError as exc:
            self._sync_failed(
                tr("Read succeeded, but the profile could not be saved: {detail}").format(
                    detail=exc
                )
            )
            return
        self._refresh_all()
        self._finish_sync(tr("Adopted the keypad profile"))

    def _finish_sync(self, message: str) -> None:
        self._syncing = False
        self._profiles_diverged = False
        self._refresh_connection()
        self.statusBar().showMessage(message)

    def _sync_failed(self, message: str) -> None:
        self._syncing = False
        self._profiles_diverged = True
        self._refresh_connection()
        QMessageBox.critical(self, tr("Sync failed"), message)

    def _disconnect(self) -> None:
        # Asked for, so do not undo it. `_poll_connection` sees the same
        # transition a dropped cable makes and must be able to tell them apart.
        self._reconnect_allowed = False
        self._reconnect_at = None
        self.app.disconnect()
        self.statusBar().showMessage(tr("Disconnected"))
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
            self.statusBar().showMessage(tr("Keypad disconnected; looking for it again…"))
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
        self.preserve_key_timing.setEnabled(enabled)
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
            self.connect_button.setText(tr("Connecting..."))
            self.connect_button.setEnabled(False)
            self.port_box.setEnabled(False)
            self.sync_button.setEnabled(False)
            self._set_editing_enabled(False)
            return

        connected = self.app.device.connected
        self.connect_button.setText(tr("Disconnect") if connected else tr("Connect"))
        recording = self.session.recording
        busy = recording or self._syncing or self._resetting
        self.connect_button.setEnabled(not busy)
        self.port_box.setEnabled(not connected and not busy)
        self.sync_button.setEnabled(connected and not busy)
        self.sync_button.setText(
            tr("Sync needed") if self._profiles_diverged else tr("Sync…")
        )
        self._set_editing_enabled(not busy)
        if connected:
            hello = getattr(self.app.device, "hello", None)
            firmware = (
                tr(" - firmware {firmware}").format(firmware=hello.firmware)
                if hello is not None
                else ""
            )
            suffix = tr(" · profiles differ") if self._profiles_diverged else ""
            port = getattr(self.app.device, "port", "keypad")
            self.link_label.setText(f"{port}{firmware}{suffix}")
        else:
            self.link_label.setText(tr("no keypad"))

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
            self.statusBar().showMessage(tr("Auto-connect is disabled"))
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
            self.statusBar().showMessage(
                tr("{port} is gone; looking for the keypad").format(port=chosen)
            )
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
                self.statusMessage.emit(tr("Recording is ready"))
            return
        if self.app.settings.capture_setup_declined and not force:
            return

        before = status()
        detail = before.reason or tr("Recording cannot see the keyboard on this session.")
        answer = QMessageBox.question(
            self,
            tr("Enable recording?"),
            tr(
                "{detail}\n\n"
                "Allow macroKey to set this up? You will be asked for your "
                "administrator password once. This grants your account access to "
                "all keyboard and mouse input, including passwords; macroKey opens "
                "that input only while the pixel and banner show recording. The "
                "keypad still works either way — only recording needs this."
            ).format(detail=detail),
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
            self.statusMessage.emit(
                tr("Recording setup skipped — hold-to-record will not capture")
            )
            return

        self.app.settings.capture_setup_declined = False
        self.app.settings.save()
        self._capture_setup_running = True
        self._refresh_connection()
        self.statusMessage.emit(tr("Preparing recording support…"))

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
            self.statusMessage.emit(tr("Recording is ready"))
            QMessageBox.information(
                self,
                tr("Recording ready"),
                tr(
                    "Hold a key for 3 seconds to record. "
                    "If a brand-new keyboard appears after reboot and recording "
                    "fails again, log out and back in once so the input group applies."
                ),
            )
            return

        QMessageBox.warning(
            self,
            tr("Could not finish setup"),
            tr(
                "{message}\n\n"
                "You can retry next launch, or run:\n"
                "  sudo usermod -aG input $USER\n"
                "then log out and back in."
            ).format(message=message),
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
            shown_gesture = tr(gesture)
            self.statusBar().showMessage(
                tr("Recording into key {key} ({gesture}) - hold it again to finish").format(
                    key=key, gesture=shown_gesture
                )
            )
            self.record_banner.setText(
                tr(
                    "  ● RECORDING key {key} · {gesture} — hold the same key again "
                    "to finish  "
                ).format(key=key, gesture=shown_gesture)
            )
            self.capture_title.setText(
                tr("Recording key {key} · {gesture}").format(key=key, gesture=shown_gesture)
            )
            self.capture_list.clear()
            self.capture_list.addItem(tr("(listening…)"))
        outcome = session.last_outcome
        if outcome is not None and not session.recording:
            self._refresh_all()
            self._show_capture(outcome)
            # Deferred so it opens over a window that has finished redrawing
            # the recording it is about, rather than during it.
            QTimer.singleShot(600, self._offer_flat_pointer_for_fast_macro)
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
            and self.capture_list.item(0).text() == tr("(listening…)")
        ):
            self.capture_list.clear()
        self.capture_list.addItem(line)
        self.capture_list.scrollToBottom()

    def _show_capture(self, outcome) -> None:
        """Lists what the last recording actually caught.

        A recording is authored blind: the pad has no screen and the window need
        not even be open. When what was captured is not what was done there was
        nothing to look at, so the only move was to guess -- and "it moved on
        its own" is not a thing anyone can debug from a status bar.
        """
        where = outcome.where or outcome.error or tr("nothing was captured")
        self.capture_title.setText(
            tr("Key {key} {gesture} - {where}").format(
                key=outcome.key + 1, gesture=tr(outcome.gesture), where=where
            )
        )

        self.capture_list.clear()
        if outcome.dropped_secrets:
            self.capture_list.addItem(
                tr("! {count} step(s) removed: looked like a password").format(
                    count=outcome.dropped_secrets
                )
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
                self.capture_list.addItem(tr("(nothing)"))
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

        self.capture_list.addItems(lines or [tr("(nothing)")])

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._closing:
            super().closeEvent(event)
            return
        self._closing = True
        dialog = self._open_question
        self._open_question = None
        if dialog is not None:
            dialog.close()
        self._connection_timer.stop()
        self.app.close()
        self._io_queue.put(None)
        thread, self._io_thread = self._io_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        super().closeEvent(event)


#: Where the project lives. Shown in About, and the only place in the app that
#: points anywhere outside it.
MADUINOS_URL = "https://maduinos.blogspot.com/"

#: Longest release note a box gets. Past this it stops being something read on
#: the way back to work and becomes a wall nobody finishes; the full text is on
#: the releases page either way.
RELEASE_SUMMARY_MAX_CHARS = 1200


def _release_summary(notes: str) -> str:
    """The "what changed" half of a release body, as plain text.

    The workflow writes the changelog entry, a `---`, and then the file listing
    that belongs to the releases page rather than to a dialog. Everything above
    the rule is what someone wants here.

    Markdown is flattened rather than rendered: `**bold**` and backticks are
    punctuation in a text box, not emphasis, and a release written by hand may
    contain neither. Anything unrecognised passes through untouched, so a body
    from some other repository (`MACROKEY_UPDATE_REPO`) still reads as itself.
    """
    body = notes.split("\n---", 1)[0].strip()
    if not body:
        return ""
    body = re.sub(r"\*\*(.+?)\*\*", r"\1", body)
    body = body.replace("`", "")
    if len(body) > RELEASE_SUMMARY_MAX_CHARS:
        body = body[:RELEASE_SUMMARY_MAX_CHARS].rsplit("\n", 1)[0].rstrip() + "\n…"
    return body


def _apply_window_icon(qt_app: QApplication) -> None:
    """Give every window the app icon, dialogs and message boxes included.

    Windows takes the taskbar icon from the exe, so this is what fixes Linux and
    every secondary window on both. A build packaged without the asset keeps the
    toolkit default rather than failing to open.
    """
    icon = QIcon(resource_path(os.path.join("assets", "app_icon.png")))
    if not icon.isNull():
        qt_app.setWindowIcon(icon)


def run_gui(port: str = "") -> int:
    qt_app = QApplication.instance() or QApplication(sys.argv)
    _apply_window_icon(qt_app)
    # Before the first widget exists. Labels, tooltips and the pinned button
    # widths are all computed during construction, so a language chosen after
    # this point would only reach whatever is built later.
    set_language(Settings.load().language)
    window = MainWindow(port=port)
    window.show()
    return qt_app.exec()
