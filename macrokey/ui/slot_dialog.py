"""The one-key editor: shortcut binding, and a pointer at hold-to-record."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
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
from ..i18n import tr
from .describe import describe_binding
from .widgets import ShortcutEdit, heading

#: The dialog opens at 560 px; this is that minus the layout margins, and it is
#: the width the record hint is measured against. Only used for that measurement.
_HINT_WRAP_WIDTH = 520


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

        self.setWindowTitle(
            tr("Key {key} · {gesture}").format(key=key + 1, gesture=tr(gesture))
        )
        self.setModal(True)
        self.resize(560, 400)

        current = app.profile.action(key, gesture)
        self.now = QLabel(
            tr("Now: {description}").format(
                description=describe_binding(app.profile, current)
            )
        )
        self.now.setWordWrap(True)
        self.now.setStyleSheet("font-weight: 600;")

        # ---- shortcut ---------------------------------------------------------
        self.shortcut = ShortcutEdit()
        if current.kind == "key":
            self.shortcut.setText(current.hotkey)
        self.shortcut.changed.connect(lambda _v: self.shortcut.stop_capture())
        press_keys = QPushButton(tr("Press keys"))
        press_keys.setToolTip(
            tr(
                "Fills the field from the next combination pressed. The field can "
                "also just be typed into."
            )
        )
        press_keys.clicked.connect(self.shortcut.start_capture)
        set_shortcut = QPushButton(tr("Set"))
        set_shortcut.clicked.connect(self._use_shortcut)
        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(self.shortcut, 1)
        shortcut_row.addWidget(press_keys)
        shortcut_row.addWidget(set_shortcut)

        # ---- direct mouse actions --------------------------------------------
        mouse_grid = QGridLayout()
        for index, (label, kind, value) in enumerate(
            (
                (tr("Left click"), "mouse_button", "left"),
                (tr("Right click"), "mouse_button", "right"),
                (tr("Middle click"), "mouse_button", "middle"),
                (tr("Wheel up"), "mouse_wheel", 1),
                (tr("Wheel down"), "mouse_wheel", -1),
            )
        ):
            button = QPushButton(label)
            button.setToolTip(
                tr(
                    "Runs at the current pointer. This direct action does not move "
                    "or home the cursor."
                )
            )
            button.clicked.connect(
                lambda _checked=False, kind=kind, value=value: self._use_mouse(
                    kind, value
                )
            )
            mouse_grid.addWidget(button, index // 3, index % 3)

        # ---- recording (settings only) ----------------------------------------
        self.capture_mouse = QCheckBox(tr("Include mouse when recording"))
        self.capture_mouse.setChecked(bool(app.settings.recorder_capture_mouse))
        self.capture_mouse.setToolTip(
            tr(
                "Records clicks, wheel, and pointer movement. By default clicks use "
                "the current pointer and movement is relative. Applies to the next "
                "hold-to-record on the pad."
            )
        )
        self.capture_mouse.toggled.connect(self._mouse_setting_changed)

        self.anchor_mouse = QCheckBox(tr("Replay from a fixed screen position (experimental)"))
        self.anchor_mouse.setChecked(bool(app.settings.recorder_anchor_mouse))
        self.anchor_mouse.setEnabled(self.capture_mouse.isChecked())
        self.anchor_mouse.setToolTip(
            tr(
                "Moves the pointer to the top-left before both recording and replay. "
                "Use only with the same monitor layout, scaling, pointer speed, and "
                "window positions; otherwise the click can land elsewhere."
            )
        )
        self.anchor_mouse.toggled.connect(self._anchor_setting_changed)

        if gesture == "double":
            trigger = tr(
                "Tap key {key}, then press and hold it within 250 ms for 3 seconds"
            ).format(key=key + 1)
        else:
            trigger = tr("Hold key {key} on its own for 3 seconds").format(key=key + 1)
        self.record_hint = QLabel(
            tr(
                "{trigger} to record into this {gesture} slot (pixel turns red). "
                "Hold the same key again to finish. What is captured appears in the "
                "main window as it happens."
            ).format(trigger=trigger, gesture=tr(gesture))
        )
        self.record_hint.setWordWrap(True)
        self.record_hint.setStyleSheet("color: palette(window-text);")
        # QLabel's wrapped minimum-size hint undercounts by roughly two lines
        # with a 15 pt desktop font, and the bottom buttons then cover the most
        # important part (how to finish).
        #
        # Four lines is the floor the English copy was given, and it is kept:
        # it was never an exact wrap count, it was slack against that
        # undercount. The measured height is taken when it is larger, which is
        # what a translation that wraps to more lines needs -- Korean sets this
        # sentence in a different number of lines, and a fixed four would bury
        # the same sentence the four was chosen to protect.
        wrapped = self.record_hint.fontMetrics().boundingRect(
            QRect(0, 0, _HINT_WRAP_WIDTH, 0),
            Qt.TextWordWrap,
            self.record_hint.text(),
        )
        floor = self.record_hint.fontMetrics().lineSpacing() * 4
        self.record_hint.setMinimumHeight(max(wrapped.height(), floor) + 4)

        # ---- bottom -----------------------------------------------------------
        clear = QPushButton(tr("Clear {gesture} binding").format(gesture=tr(gesture)))
        clear.clicked.connect(self._clear)
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(clear)
        bottom.addStretch(1)
        bottom.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(self.now)
        layout.addSpacing(8)
        layout.addWidget(heading(tr("Send a shortcut")))
        layout.addLayout(shortcut_row)
        layout.addSpacing(10)
        layout.addWidget(heading(tr("Or use the mouse at its current position")))
        layout.addLayout(mouse_grid)
        layout.addSpacing(10)
        layout.addWidget(heading(tr("Or replay something you do")))
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
            QMessageBox.critical(
                self, tr("That is not a shortcut this keypad can send"), str(exc)
            )
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
