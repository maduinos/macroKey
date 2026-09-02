"""The two-row toolbar at UI fonts bigger than the one it was drawn against.

Also at languages other than the one it was drawn against. Both are the same
failure: a control sized against text that is not the text it ends up showing.
A button elides rather than refusing to shrink, so this does not crash or warn
anywhere -- the label just quietly loses its end, and "연결 해..." still looks
like a button.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey import i18n  # noqa: E402
from macrokey.translations import TRANSLATIONS  # noqa: E402
from macrokey.ui.app import MainWindow  # noqa: E402

#: 9 pt is what the layout was drawn against; the desktop defaults above it are
#: where it used to break.
FONT_SIZES = (9, 11, 13, 15)

#: English plus everything with a table. Parametrised rather than hard-coded so
#: a new language is covered by adding it to `TRANSLATIONS`, not by remembering
#: to come back here.
LANGUAGES = ("en", *sorted(TRANSLATIONS))


@pytest.fixture
def build_window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_autoconnect", lambda self: None)
    # Takes **kwargs: a deferred `singleShot(750, ... force=force)` from an
    # earlier window can land inside a later test's processEvents, and a
    # positional-only stub raises TypeError from inside Qt's event loop.
    monkeypatch.setattr(MainWindow, "_maybe_fix_capture", lambda self, **_kwargs: None)
    original = app.font()
    windows: list[MainWindow] = []

    def build(point_size: int, language: str = "en") -> MainWindow:
        # Before the window: every label and pinned width is decided in
        # __init__, which is also why the editor asks for a restart.
        i18n.set_language(language)
        font = app.font()
        font.setPointSizeF(point_size)
        app.setFont(font)
        window = MainWindow()
        window._connection_timer.stop()
        # Ask for the old fixed minimum; the real one should overrule it.
        window.resize(820, 560)
        window.show()
        app.processEvents()
        windows.append(window)
        return window

    yield build
    for window in windows:
        window.close()
        window.deleteLater()
    app.processEvents()
    app.setFont(original)
    i18n.set_language("en")


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("point_size", FONT_SIZES)
def test_no_toolbar_widget_is_squeezed_below_its_label(
    build_window, point_size, language
) -> None:
    window = build_window(point_size, language)

    squeezed = [
        name
        for name in ("port_box", "connect_button", "text_speed", "reset_button", "sync_button")
        if getattr(window, name).width() < getattr(window, name).sizeHint().width()
    ]

    assert squeezed == []


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("point_size", FONT_SIZES)
def test_the_window_cannot_be_narrower_than_its_toolbar(
    build_window, point_size, language
) -> None:
    """resize() must not make controls overlap or labels elide."""
    window = build_window(point_size, language)

    assert window.width() >= window._toolbar.sizeHint().width()


@pytest.mark.parametrize("language", LANGUAGES)
def test_large_text_does_not_force_a_wider_than_small_screen_window(
    build_window, language
) -> None:
    window = build_window(15, language)
    assert window.minimumWidth() <= 800


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("point_size", FONT_SIZES)
def test_state_swapping_labels_still_fit(build_window, point_size, language) -> None:
    """Connect and Sync change their text after the layout has settled.

    They are pinned to the widest label they will ever carry, so this is a
    check that the pinning used the *translated* labels: a width computed from
    "Connect" is not wide enough for "연결 해제", and nothing about the elided
    result says the word was cut.
    """
    window = build_window(point_size, language)

    for button, labels in (
        (
            window.connect_button,
            (i18n.tr("Connect"), i18n.tr("Disconnect"), i18n.tr("Connecting...")),
        ),
        (window.sync_button, (i18n.tr("Sync…"), i18n.tr("Sync needed"))),
    ):
        for label in labels:
            button.setText(label)
            assert button.width() >= button.sizeHint().width(), (label, language)


# ------------------------------------------------------------ slot dialog --
#
# The one-key editor has the same hazard in a different shape: its recording
# hint is a wrapped paragraph with buttons underneath, and the height reserved
# for it used to be a hand-counted number of English lines.


@pytest.fixture
def slot_app(monkeypatch, tmp_path):
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    from macrokey.app import MacroKeyApp

    instance = MacroKeyApp()
    yield instance
    instance.close()


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("gesture", ("tap", "double"))
@pytest.mark.parametrize("point_size", FONT_SIZES)
def test_the_recording_hint_is_not_covered_by_the_buttons_under_it(
    slot_app, monkeypatch, point_size, gesture, language
) -> None:
    from PySide6.QtCore import QRect, Qt

    from macrokey.ui.slot_dialog import _HINT_WRAP_WIDTH, SlotDialog

    qt_app = QApplication.instance()
    original = qt_app.font()
    font = qt_app.font()
    font.setPointSizeF(point_size)
    qt_app.setFont(font)
    i18n.set_language(language)
    try:
        dialog = SlotDialog(None, slot_app, 0, gesture)
        needed = dialog.record_hint.fontMetrics().boundingRect(
            QRect(0, 0, _HINT_WRAP_WIDTH, 0),
            Qt.TextWordWrap,
            dialog.record_hint.text(),
        )
        assert dialog.record_hint.minimumHeight() >= needed.height(), (
            language,
            gesture,
            point_size,
        )
    finally:
        qt_app.setFont(original)
        i18n.set_language("en")


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_slot_dialog_labels_are_in_the_chosen_language(slot_app, language) -> None:
    """Catches a dialog that was built before `set_language` reached it."""
    from macrokey.ui.slot_dialog import SlotDialog

    i18n.set_language(language)
    try:
        dialog = SlotDialog(None, slot_app, 0, "tap")
        assert dialog.record_hint.text().startswith(
            i18n.tr("Hold key {key} on its own for 3 seconds").format(key=1)
        )
    finally:
        i18n.set_language("en")
