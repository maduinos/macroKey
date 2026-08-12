"""The one-key editor: what this key does, and recording it by doing it."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..app import MacroKeyApp
from ..config import Action
from .describe import (
    SECRET_TEXT_LENGTH,
    describe_binding,
    longest_typed_run,
    nothing_captured_hint,
)
from .widgets import ShortcutEdit, heading


class SlotDialog(QDialog):
    """Everything one key can be, in one window.

    Binding used to be two nested dialogs: pick an action kind and a value in
    one, and if you chose to record, a second window on top of it. The kinds
    were the serial wire format showing through -- key, consumer, mouse_button,
    host -- which is not how anyone thinks about what a key should do.

    There are two answers. Send a shortcut, which is the common one and has to
    be typeable: recording ctrl+alt+shift+5 means pressing it, which fires
    whatever is already bound to it. Or replay something, which is authored by
    doing it -- and which the pad can now author on its own, by being held.
    """

    captured = Signal(list)
    liveEvent = Signal(str)

    def __init__(self, parent: QWidget, app: MacroKeyApp, key: int, gesture: str):
        super().__init__(parent)
        self.app = app
        self.key, self.gesture = key, gesture
        self.result_action: Action | None = None
        self.recorded_steps: list[dict] | None = None
        self._recording = False

        self.setWindowTitle(f"Key {key + 1} · {gesture}")
        self.setModal(True)
        self.resize(520, 470)

        current = app.profile.action(key, gesture)
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
            "Records clicks, wheel, and pointer movement (when the capture "
            "backend can see them). Replay homes the cursor to the top-left "
            "first so clicks land on the same pixels."
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
        layout.addWidget(heading("Send a shortcut"))
        layout.addLayout(shortcut_row)
        layout.addSpacing(10)
        layout.addWidget(heading("Or replay something you do"))
        layout.addLayout(record_row)
        layout.addWidget(self.log, 1)
        layout.addWidget(self.where)
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

        session = getattr(self.app, "session", None)
        if session is not None and session.recording:
            QMessageBox.warning(
                self,
                "Already recording",
                "Finish the pad hold-to-record (hold the same key again) before "
                "recording from this window.",
            )
            return

        self.app.recorder.capture_mouse = self.capture_mouse.isChecked()
        # Stopping is a button, so without this the click that ends the
        # recording is its last step, and every replay would finish by
        # clicking wherever that button happened to be.
        self._sync_ignored_region()
        if self.app.recorder.capture_mouse and self.app.device.connected:
            try:
                self.app.device.home_pointer()
            except Exception:  # noqa: BLE001 - keyboard-only still works
                pass
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

        if not steps:
            self.use_recording.setEnabled(False)
            self.log.addItem("(nothing captured)")
            self.where.setText(nothing_captured_hint())
            return

        if self.app.last_redacted:
            # Say it plainly. A step vanishing without explanation looks like a
            # capture bug, and the person needs to know their password was in
            # range of the recorder so they can judge whether to change it.
            self.log.addItem(
                f"!  {self.app.last_redacted} step(s) removed: a password prompt "
                "was answered while recording"
            )

        typed = longest_typed_run(steps)
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
            # Still allow Use — the warning is enough; discard is Cancel.
            fits = self.app.recording_fits(steps, self.key, self.gesture)
            self.use_recording.setEnabled(fits)
            return
        if self.app.recorder.device_action(steps) is not None:
            self.where.setText("Will be stored on the keypad. Works with nothing running here.")
            self.use_recording.setEnabled(True)
        elif self.app.recording_fits(steps, self.key, self.gesture):
            self.where.setText(
                f"Will be stored on the keypad as a {len(steps)} step macro. "
                "Works with nothing running here."
            )
            self.use_recording.setEnabled(True)
        else:
            self.use_recording.setEnabled(False)
            self.where.setText(
                "Too large for the keypad (macro slots full or steps it cannot "
                "replay). Shorten the recording or clear unused keys, then try again."
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

    def _clear(self) -> None:
        self.result_action = Action()
        self.accept()

    def reject(self) -> None:
        if self._recording:
            self._recording = False
            self.app.stop_recording()
        super().reject()


