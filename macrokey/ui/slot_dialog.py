"""The one-key editor: what a shortcut this key sends."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..app import MacroKeyApp
from ..config import Action, ProfileError
from ..config.keycodes import KeyParseError, parse_hotkey
from ..config.model import (
    MAX_MACRO_REPEAT,
    MIN_MACRO_REPEAT,
    SHORTCUT_REPEAT_GAP_MS,
)
from ..i18n import tr
from .describe import describe_binding, format_duration, macro_pass_ms
from .key_grab import KeyGrab
from .widgets import ShortcutEdit, heading

#: The dialog opens at 560 px; this is that minus the layout margins, and it is
#: the width the status line is measured against. Only used for that measurement.
_HINT_WRAP_WIDTH = 520

#: A backend's own words, cut to what the status line has room for. The reason
#: matters -- it is the half that says what to fix -- but it is not the whole
#: sentence, and the height reserved below is measured against this length.
_REASON_LIMIT = 160


class SlotDialog(QDialog):
    """What one key sends, in one window.

    Binding used to be two nested dialogs: pick an action kind and a value in
    one, and if you chose to record, a second window on top of it. The kinds
    were the serial wire format showing through -- key, consumer, mouse_button,
    host -- which is not how anyone thinks about what a key should do.

    What is left is the one question worth asking here: which shortcut. Mouse
    actions and the recording switches used to sit underneath it; both were
    settings for other features wearing the shape of a binding, and recording is
    driven by the pad and reported by the main window anyway.

    "Press keys" reads the keyboard through `KeyGrab` rather than through Qt, so
    a key that the desktop normally keeps for itself is still bindable, and the
    combination being bound does not fire whatever it is bound to today.
    """

    def __init__(self, parent: QWidget, app: MacroKeyApp, key: int, gesture: str):
        super().__init__(parent)
        self.app = app
        self.key, self.gesture = key, gesture
        self.result_action: Action | None = None
        #: Set when the repeat row already wrote the binding through the app.
        #: The caller then persists and pushes without replacing anything.
        self.repeat_applied = False
        self._before_grab = ""

        self.setWindowTitle(
            tr("Key {key} · {gesture}").format(key=key + 1, gesture=tr(gesture))
        )
        self.setModal(True)
        self.resize(560, 300)

        current = self.current = app.profile.action(key, gesture)
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
        self.press_keys = QPushButton(tr("Press keys"))
        self.press_keys.setToolTip(
            tr(
                "Fills the field from the next combination pressed on the keyboard. "
                "The field can also just be typed into."
            )
        )
        self.press_keys.clicked.connect(self._press_keys)
        set_shortcut = QPushButton(tr("Set"))
        set_shortcut.clicked.connect(self._use_shortcut)
        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(self.shortcut, 1)
        shortcut_row.addWidget(self.press_keys)
        shortcut_row.addWidget(set_shortcut)

        # ---- the line under the field ----------------------------------------
        # `key_grab`, not `grab`: QWidget.grab() renders a widget to a pixmap,
        # and an attribute of that name shadows it.
        self.key_grab = KeyGrab(self)
        self.key_grab.progress.connect(self.shortcut.setText)
        self.key_grab.captured.connect(self._captured)
        self.key_grab.finished.connect(self._grab_finished)

        self.hint = QLabel(self._idle_hint())
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(window-text);")
        # QLabel's wrapped minimum-size hint undercounts by roughly two lines
        # with a 15 pt desktop font, and the buttons underneath then cover the
        # line that says what is happening to the keyboard right now.
        #
        # Measured against every sentence this label can hold rather than a
        # hand-counted number of English lines: the messages differ in length,
        # and a translation sets the same sentence in a different number of
        # lines -- Korean does -- so a fixed count would bury one of them.
        metrics = self.hint.fontMetrics()
        tallest = max(
            metrics.boundingRect(
                QRect(0, 0, _HINT_WRAP_WIDTH, 0), Qt.TextWordWrap, text
            ).height()
            for text in self.possible_hints()
        )
        self.hint.setMinimumHeight(tallest + 4)

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
        layout.addWidget(self.hint)

        # ---- repeat -----------------------------------------------------------
        # Shown for every key, not only the ones holding a recording. Hiding the
        # row made the feature unfindable: a pad out of the box has eight
        # shortcuts and no recordings at all, so every key opened this window
        # with the row absent and nothing anywhere saying what would bring it
        # back. Disabled, it says what this key is missing instead.
        #
        # Here rather than as a third column in the key grid, where a number
        # between Tap and Double would not say which of them it belonged to.
        layout.addSpacing(8)
        layout.addWidget(heading(tr("Repeat the recording")))
        self.repeat = QSpinBox()
        self.repeat.setRange(MIN_MACRO_REPEAT, MAX_MACRO_REPEAT)
        self.repeat.setValue(current.repeat)
        self.repeat.setToolTip(
            tr(
                "How many times one press replays the recording. The pad "
                "stops a repeat early when any of its keys is pressed."
            )
        )
        self.repeat.valueChanged.connect(self._repeat_changed)
        self.repeat_cost = QLabel()
        self.repeat_cost.setWordWrap(True)
        self.set_repeat = QPushButton(tr("Set"))
        self.set_repeat.clicked.connect(self._use_repeat)

        # A shortcut can be repeated too: asking for it wraps the shortcut in a
        # one-shortcut macro, because a macro is the only thing the pad knows how
        # to replay more than once. Only an empty key has nothing to repeat.
        repeatable = current.kind in ("sequence", "key")
        self.repeat.setEnabled(repeatable)
        self.set_repeat.setEnabled(repeatable)

        repeat_row = QHBoxLayout()
        repeat_row.addWidget(self.repeat)
        repeat_row.addWidget(self.repeat_cost, 1)
        repeat_row.addWidget(self.set_repeat)
        layout.addLayout(repeat_row)
        self._repeat_changed(current.repeat)

        layout.addStretch(1)
        layout.addLayout(bottom)

    # ----------------------------------------------------------------- status --
    #
    # One line under the field, and it is the only place this window says what
    # is happening to the keyboard. Each message is its own method so the
    # English source stays a literal `tr` argument -- the translation check
    # reads the source for those, and a table keyed by a variable is invisible
    # to it.

    def _idle_hint(self) -> str:
        return tr(
            "Press keys reads the keyboard itself, so every key arrives as the "
            "key it is. The field can also just be typed into."
        )

    def _listening_hint(self, *, alone: bool) -> str:
        if alone:
            return tr(
                "Listening. The combination is taken here, so it does not also "
                "do whatever it is bound to today."
            )
        return tr(
            "Listening. These keys reach the desktop too, so a combination it "
            "owns will act on it while you press it."
        )

    def _window_only_hint(self, reason: str) -> str:
        return tr(
            "Reading this window only ({reason}). A key the desktop takes first "
            "never arrives here; type that one into the field."
        ).format(reason=reason)

    def _unsendable_hint(self, value: str, reason: str) -> str:
        return tr("Read {value}, but this keypad cannot send it: {reason}").format(
            value=value, reason=reason
        )

    def possible_hints(self) -> list[str]:
        """Every sentence the status line can hold, in the active language.

        The label reserves height for the tallest of these, so this is what the
        layout is sized against as well as a list of what the line can say. The
        two that carry a backend's own words are measured with filler as long as
        that text is allowed to be.
        """
        # Spaced words, not one long token: a 160-character run of "x" cannot
        # be broken, so it would measure as a single line and reserve nothing.
        filler = " ".join(["xxxxxxxx"] * (_REASON_LIMIT // 9))
        return [
            self._idle_hint(),
            self._listening_hint(alone=True),
            self._listening_hint(alone=False),
            self._window_only_hint(filler),
            self._unsendable_hint("ctrl+shift+x", filler),
        ]

    def _say(self, text: str) -> None:
        self.hint.setText(text)

    # ------------------------------------------------------------ reading keys --

    def _press_keys(self) -> None:
        """Reads the real keyboard, or says why it is reading this window instead."""
        if self.key_grab.active:
            self.key_grab.stop()
            return

        if self.app.recorder.recording:
            # A hold-to-record is running on the pad. Taking the keyboard
            # exclusively would take those events away from it, and the macro
            # would silently lose whatever was typed while this window listened.
            self.shortcut.start_capture()
            self._say(self._window_only_hint(tr("a recording is running")))
            return

        # What the field held before it was cleared to listen. Stopping without
        # pressing anything -- or waiting out the timeout -- puts it back, so
        # starting to rebind a key and changing your mind does not silently
        # empty the field the Set button reads.
        self._before_grab = self.shortcut.text()
        self.shortcut.begin_grab()
        started, reason = self.key_grab.start()
        if not started:
            # Qt's own capture is the fallback, not an error: it binds most keys
            # perfectly well, and the line underneath says what it cannot do.
            self.shortcut.start_capture()
            self._say(self._window_only_hint(reason[:_REASON_LIMIT]))
            return

        self.press_keys.setText(tr("Stop"))
        self._say(self._listening_hint(alone=self.key_grab.exclusive))

    def _captured(self, value: str) -> None:
        self.shortcut.setText(value)
        try:
            parse_hotkey(value)
        except KeyParseError as exc:
            self._say(self._unsendable_hint(value, str(exc)[:_REASON_LIMIT]))
            return
        self._say(self._idle_hint())

    def _grab_finished(self, reason: str) -> None:
        """Listening has stopped, whatever ended it."""
        self.press_keys.setText(tr("Press keys"))
        self.shortcut.stop_capture()
        # Modifiers alone are the shortcut half-built, not a shortcut; a grab
        # that ended there captured nothing. When one was captured, `captured`
        # follows this and writes it over whatever is restored here.
        if not self.shortcut.text().strip("+"):
            self.shortcut.setText(self._before_grab)
        if reason:
            self._say(reason)

    def done(self, result: int) -> None:  # noqa: D102 - QDialog's own docstring
        # Every way out of this window, including Esc and the close button. The
        # grab holds the keyboard away from the desktop, so it must not outlive
        # the window that started it.
        self.key_grab.stop()
        super().done(result)

    # ---------------------------------------------------------------- results --

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

    def _repeat_changed(self, value: int) -> None:
        """How long that many passes takes, next to the number that asked."""
        if self.current.kind not in ("sequence", "key"):
            self.repeat_cost.setText(tr("Bind a shortcut or record into this key first."))
            return
        if value <= MIN_MACRO_REPEAT:
            self.repeat_cost.setText(tr("once"))
            return
        if self.current.kind == "key":
            # Not stored yet: this is what the wrapping will pace it at.
            pass_ms = SHORTCUT_REPEAT_GAP_MS
        else:
            pass_ms = macro_pass_ms(self.app.profile, self.current.slot)
        self.repeat_cost.setText(format_duration(pass_ms * value))

    def _use_repeat(self) -> None:
        """Applies the count through the app, which owns macro slot allocation.

        Unlike this window's other exits it does not hand back an action for the
        caller to store: repeating a shortcut has to put a macro somewhere, and
        which slot is free is not something a dialog knows. `set_repeat` reads
        the binding itself, which also settles the stale-state problem -- the
        pad drives recording on a background thread and its signals are
        delivered while this modal dialog is up, so the key being edited can be
        rebound between opening this window and pressing Set.
        """
        try:
            self.app.set_repeat(self.key, self.gesture, self.repeat.value())
        except ProfileError as exc:
            QMessageBox.critical(self, tr("That repeat will not fit"), str(exc))
            return
        self.repeat_applied = True
        self.accept()

    def _clear(self) -> None:
        self.result_action = Action()
        self.accept()
