"""The one-key editor: shortcut binding, and a pointer at hold-to-record."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..app import MacroKeyApp
from ..config import Action
from .describe import describe_binding
from .widgets import ShortcutEdit, heading


class SlotDialog(QDialog):
    """Everything one key can be, in one window.

    Binding used to be two nested dialogs: pick an action kind and a value in
    one, and if you chose to record, a second window on top of it. The kinds
    were the serial wire format showing through -- key, consumer, mouse_button,
    host -- which is not how anyone thinks about what a key should do.

    Recording itself is driven by the pad (hold 3 s, hold again to finish).
    This window only sets the shortcut, toggles mouse capture, and points at
    the main window's live log -- starting capture from a button here meant
    the Stop click could land in the macro, and duplicated the pad's job.
    """

    def __init__(self, parent: QWidget, app: MacroKeyApp, key: int, gesture: str):
        super().__init__(parent)
        self.app = app
        self.key, self.gesture = key, gesture
        self.result_action: Action | None = None

        self.setWindowTitle(f"Key {key + 1} · {gesture}")
        self.setModal(True)
        self.resize(520, 320)

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

        # ---- recording (settings only) ----------------------------------------
        self.capture_mouse = QCheckBox("Include mouse when recording")
        self.capture_mouse.setChecked(bool(app.settings.recorder_capture_mouse))
        self.capture_mouse.setToolTip(
            "Records clicks, wheel, and pointer movement. Replay homes the "
            "cursor to the top-left first so clicks land on the same pixels. "
            "Applies to the next hold-to-record on the pad."
        )
        self.capture_mouse.toggled.connect(self._mouse_setting_changed)

        record_hint = QLabel(
            f"Hold key {key + 1} on the pad for 3 seconds to record into this "
            f"{gesture} slot (pixel turns red). Hold the same key again to "
            "finish. What is captured appears in the main window as it happens."
        )
        record_hint.setWordWrap(True)
        record_hint.setStyleSheet("color: palette(mid);")

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
        layout.addWidget(self.capture_mouse)
        layout.addWidget(record_hint)
        layout.addStretch(1)
        layout.addLayout(bottom)

    def _mouse_setting_changed(self, checked: bool) -> None:
        self.app.settings.recorder_capture_mouse = checked
        self.app.recorder.capture_mouse = checked
        self.app.settings.save()

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

    def _clear(self) -> None:
        self.result_action = Action()
        self.accept()
