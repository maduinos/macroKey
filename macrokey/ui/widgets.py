"""Small widgets the editor is built out of.

Each one exists because the stock control got something wrong for this job: a
shortcut typed instead of pressed, a port list that went stale the moment the
window opened.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit

from ..i18n import tr


class ShortcutEdit(QLineEdit):
    """Records a shortcut by having it pressed, the way desktop settings do.

    Typing "ctrl+alt+shift+1" means knowing the vocabulary and the separator and
    that it is "gui" rather than "super" here. Pressing the combination needs
    none of that, and it is how every shortcut setting on this desktop already
    works.

    This reads Qt key events, which is the fallback rather than the good path:
    it only ever sees what the desktop chose to deliver to this window, so the
    keys the compositor keeps for itself never arrive. `ui.key_grab` reads the
    keyboard directly and is what the "Press keys" button uses when it can; this
    stays for everywhere it cannot, and for typing a shortcut by hand.
    """

    #: Ordered as `keycodes.MODIFIER_ORDER` spells a stored hotkey, so a
    #: combination reads the same however it got into the field: pressed here,
    #: read from the keyboard, or formatted back out of the pad.
    QT_MODIFIERS = (
        (Qt.ControlModifier, "ctrl"),
        (Qt.AltModifier, "alt"),
        (Qt.ShiftModifier, "shift"),
        (Qt.MetaModifier, "gui"),
    )
    #: Qt names these differently from macroKey's vocabulary.
    QT_NAMES = {
        Qt.Key_Escape: "esc",
        Qt.Key_Return: "enter",
        Qt.Key_Enter: "enter",
        Qt.Key_Backspace: "backspace",
        Qt.Key_Delete: "delete",
        Qt.Key_Space: "space",
        Qt.Key_Tab: "tab",
        Qt.Key_Up: "up",
        Qt.Key_Down: "down",
        Qt.Key_Left: "left",
        Qt.Key_Right: "right",
        Qt.Key_Home: "home",
        Qt.Key_End: "end",
        Qt.Key_PageUp: "pageup",
        Qt.Key_PageDown: "pagedown",
        Qt.Key_Insert: "insert",
        Qt.Key_CapsLock: "capslock",
        Qt.Key_Print: "printscreen",
    }
    BARE_MODIFIERS = {
        Qt.Key_Control,
        Qt.Key_Shift,
        Qt.Key_Alt,
        Qt.Key_Meta,
        Qt.Key_AltGr,
    }

    changed = Signal(str)

    #: The empty-field prompt when nothing is being captured. An example rather
    #: than an instruction, because the field is also typed into by hand.
    IDLE_PLACEHOLDER = "ctrl+alt+shift+1"

    def __init__(self) -> None:
        super().__init__()
        self.capturing = False
        #: True while something else is reading the real keyboard for us. The
        #: keys may still arrive here as well (a passive read is not exclusive),
        #: and letting them through would either type into the field or capture
        #: the combination a second time.
        self.grabbing = False
        self.setPlaceholderText(self.IDLE_PLACEHOLDER)

    def start_capture(self) -> None:
        """Next combination pressed fills the field."""
        self.capturing = True
        self.grabbing = False
        self.clear()
        self.setPlaceholderText(tr("Press the shortcut..."))
        self.setFocus()

    def stop_capture(self) -> None:
        self.capturing = False
        self.grabbing = False
        self.setPlaceholderText(self.IDLE_PLACEHOLDER)

    def begin_grab(self) -> None:
        """The keyboard is being read directly; show it and ignore Qt's copy."""
        self.capturing = False
        self.grabbing = True
        self.clear()
        self.setPlaceholderText(tr("Press the shortcut..."))
        self.setFocus()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.grabbing:
            # Swallowed, not handled: the combination is being read from the
            # kernel, and this is the same press arriving the long way round.
            event.accept()
            return
        # Typed editing stays available. Capture is a convenience for the common
        # case, not the only way in: some shortcuts are awkward or impossible to
        # press here -- anything the compositor swallows first, or a key this
        # keyboard does not have -- and those still have to be settable.
        if not self.capturing:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key in self.BARE_MODIFIERS:
            # Held on its own it is not a shortcut yet; show it building up.
            self.setText("+".join(self._modifiers(event)) + "+" if self._modifiers(event) else "")
            return

        parts = self._modifiers(event)
        name = self.QT_NAMES.get(key)
        if name is None:
            if Qt.Key_F1 <= key <= Qt.Key_F24:
                name = f"f{key - Qt.Key_F1 + 1}"
            elif 32 < key < 127:
                # event.key() is the key before shift is applied, which is what
                # a shortcut is made of. event.text() is the glyph it produced,
                # so with shift held ctrl+alt+shift+1 arrives as "!" -- a
                # character the keypad has no key for, and one this app cannot
                # parse. The keypad sends the modifiers and the key; the shifted
                # glyph is what the receiving application makes of that.
                name = chr(key).lower()
            else:
                text = event.text()
                name = text.lower() if text and text.isprintable() and len(text) == 1 else ""
        if not name:
            return

        parts.append(name)
        value = "+".join(parts)
        self.setText(value)
        # One combination per press of the button: staying in capture mode would
        # eat the typing of anyone who then wanted to adjust it by hand.
        self.stop_capture()
        self.changed.emit(value)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.grabbing:
            event.accept()
            return
        if event.key() in self.BARE_MODIFIERS and self.text().endswith("+"):
            self.setText("")

    def _modifiers(self, event) -> list[str]:
        state = event.modifiers()
        return [name for flag, name in self.QT_MODIFIERS if state & flag]



def heading(text: str) -> QLabel:
    label = QLabel(text)
    # `palette(mid)` is a bevel/shadow colour, not secondary text. On several
    # light themes it is almost the same as the window background.
    label.setStyleSheet("font-weight: 600; color: palette(window-text);")
    return label


#: Shown in the port box when discovery should choose. A word rather than a
#: blank entry: an empty combo reads as "it failed to find anything".
AUTO_PORT = "Auto"


class RescanningComboBox(QComboBox):
    """A combo box that refreshes itself when it is opened.

    Built once, it was a snapshot from before the pad was plugged in, and the
    only way to see a new port was to restart the editor.
    """

    def __init__(self, rescan) -> None:
        super().__init__()
        self._rescan = rescan

    def showPopup(self) -> None:  # noqa: N802 - Qt naming
        self._rescan()
        super().showPopup()

