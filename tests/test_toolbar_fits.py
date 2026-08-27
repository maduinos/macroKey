"""The two-row toolbar at UI fonts bigger than the one it was drawn against."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey.ui.app import MainWindow  # noqa: E402

#: 9 pt is what the layout was drawn against; the desktop defaults above it are
#: where it used to break.
FONT_SIZES = (9, 11, 13, 15)


@pytest.fixture
def build_window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_autoconnect", lambda self: None)
    monkeypatch.setattr(MainWindow, "_maybe_fix_capture", lambda self: None)
    original = app.font()

    def build(point_size: int) -> MainWindow:
        font = app.font()
        font.setPointSizeF(point_size)
        app.setFont(font)
        window = MainWindow()
        window._connection_timer.stop()
        # Ask for the old fixed minimum; the real one should overrule it.
        window.resize(820, 560)
        window.show()
        app.processEvents()
        return window

    yield build
    app.setFont(original)


@pytest.mark.parametrize("point_size", FONT_SIZES)
def test_no_toolbar_widget_is_squeezed_below_its_label(build_window, point_size) -> None:
    window = build_window(point_size)

    squeezed = [
        name
        for name in ("port_box", "connect_button", "text_speed", "reset_button")
        if getattr(window, name).width() < getattr(window, name).sizeHint().width()
    ]

    assert squeezed == []


@pytest.mark.parametrize("point_size", FONT_SIZES)
def test_the_window_cannot_be_narrower_than_its_toolbar(build_window, point_size) -> None:
    """resize() must not make controls overlap or labels elide."""
    window = build_window(point_size)

    assert window.width() >= window._toolbar.sizeHint().width()


def test_large_text_does_not_force_a_wider_than_small_screen_window(build_window) -> None:
    window = build_window(15)
    assert window.minimumWidth() <= 800
