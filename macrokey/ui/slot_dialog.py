"""The one-key editor: shortcut binding, and a pointer at hold-to-record."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
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
        self.resize(560, 400)

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

        # ---- direct mouse actions --------------------------------------------
        mouse_grid = QGridLayout()
        for index, (label, kind, value) in enumerate(
            (
                ("Left click", "mouse_button", "left"),
                ("Right click", "mouse_button", "right"),
                ("Middle click", "mouse_button", "middle"),
                ("Wheel up", "mouse_wheel", 1),
                ("Wheel down", "mouse_wheel", -1),
            )
        ):
            button = QPushButton(label)
            button.setToolTip(
                "Runs at the current pointer. This direct action does not move "
                "or home the cursor."
            )
            button.clicked.connect(
                lambda _checked=False, kind=kind, value=value: self._use_mouse(
                    kind, value
                )
            )
            mouse_grid.addWidget(button, index // 3, index % 3)

        # ---- recording (settings only) ----------------------------------------
        self.capture_mouse = QCheckBox("Include mouse when recording")
        self.capture_mouse.setChecked(bool(app.settings.recorder_capture_mouse))
        self.capture_mouse.setToolTip(
            "Records clicks, wheel, and pointer movement. By default clicks use "
            "the current pointer and movement is relative. Applies to the next "
            "hold-to-record on the pad."
        )
        self.capture_mouse.toggled.connect(self._mouse_setting_changed)

        self.anchor_mouse = QCheckBox("Replay from a fixed screen position (experimental)")
        self.anchor_mouse.setChecked(bool(app.settings.recorder_anchor_mouse))
        self.anchor_mouse.setEnabled(self.capture_mouse.isChecked())
        self.anchor_mouse.setToolTip(
            "Moves the pointer to the top-left before both recording and replay. "
            "Use only with the same monitor layout, scaling, pointer speed, and "
            "window positions; otherwise the click can land elsewhere."
        )
        self.anchor_mouse.toggled.connect(self._anchor_setting_changed)

        if gesture == "double":
            trigger = (
                f"Tap key {key + 1}, then press and hold it within 250 ms for "
                "3 seconds"
            )
        else:
            trigger = f"Hold key {key + 1} on its own for 3 seconds"
        self.record_hint = QLabel(
            f"{trigger} to record into this {gesture} slot (pixel turns red). "
            "Hold the same key again to finish. What is captured appears in the "
            "main window as it happens."
        )
        self.record_hint.setWordWrap(True)
        self.record_hint.setStyleSheet("color: palette(window-text);")
        # QLabel's wrapped minimum-size hint undercounts by roughly two lines
        # with a 15 pt desktop font, and the bottom buttons then cover the most
        # important part (how to finish). Reserve the four lines this copy uses
        # at the dialog's minimum width.
        self.record_hint.setMinimumHeight(
            self.record_hint.fontMetrics().lineSpacing() * 4 + 4
        )

        # ---- bottom -----------------------------------------------------------
        clear = QPushButton(f"Clear {gesture} binding")
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
        layout.addWidget(heading("Or use the mouse at its current position"))
        layout.addLayout(mouse_grid)
        layout.addSpacing(10)
        layout.addWidget(heading("Or replay something you do"))
        layout.addWidget(self.capture_mouse)
        layout.addWidget(self.anchor_mouse)
        layout.addWidget(self.record_hint)
        layout.addStretch(1)
        layout.addLayout(bottom)

    def _mouse_setting_changed(self, checked: bool) -> None:
        self.app.settings.recorder_capture_mouse = checked
        self.app.recorder.capture_mouse = checked
        self.anchor_mouse.setEnabled(checked)
        self.app.settings.save()

    def _anchor_setting_changed(self, checked: bool) -> None:
        self.app.settings.recorder_anchor_mouse = checked
        self.app.recorder.anchor_mouse = checked
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

    def _use_mouse(self, kind: str, value: str | int) -> None:
        if kind == "mouse_button":
            self.result_action = Action(kind=kind, button=str(value))
        else:
            self.result_action = Action(kind=kind, delta=int(value))
        self.accept()

    def _clear(self) -> None:
        self.result_action = Action()
        self.accept()
