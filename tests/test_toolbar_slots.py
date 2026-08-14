"""Toolbar slots, and the argument Qt quietly passes them.

`clicked` carries a `checked` boolean. A slot with an optional positional
parameter therefore receives False rather than its default, which is how "Write
to device" once ended every write with "'bool' object is not callable". Nothing
failed at connect time and nothing failed on the click -- it broke at the end,
in a worker callback.

Write, Read and Save are gone: every edit saves and writes itself, and
connecting reconciles the pad. The check still matters for what is left.
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from macrokey.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def window():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    return MainWindow()


CLICKED_SLOTS = ("_toggle_connection", "_brightness_settled")


@pytest.mark.parametrize("name", CLICKED_SLOTS)
def test_a_button_slot_takes_no_optional_positional_argument(window, name) -> None:
    """Otherwise Qt fills it with `checked` and the default never applies."""
    signature = inspect.signature(getattr(window, name))
    offenders = [
        parameter.name
        for parameter in signature.parameters.values()
        if parameter.kind is parameter.POSITIONAL_OR_KEYWORD
        and parameter.default is not inspect.Parameter.empty
    ]
    assert not offenders, f"{name} would receive checked as {offenders[0]}"


def test_the_toolbar_offers_no_manual_sync(window) -> None:
    """They could only repeat work already done, or be forgotten, which is worse."""
    labels = {
        button.text()
        for button in window.findChildren(QPushButton)
        if button.text()
    }
    assert "Write to device" not in labels
    assert "Read from device" not in labels
    assert "Save" not in labels


def test_connecting_is_still_offered(window) -> None:
    assert window.connect_button.text() in ("Connect", "Disconnect", "Connecting...")


def test_the_port_can_still_be_chosen_by_hand(window) -> None:
    """Auto-connect picks the port; it is not the only thing that may. Two
    boards, a slow board, a port that has to be named -- when the guess is
    wrong there has to be somewhere to say so."""
    from macrokey.ui.app import AUTO_PORT

    assert window.port_box.isEditable()
    assert AUTO_PORT in [window.port_box.itemText(i) for i in range(window.port_box.count())]


def test_auto_means_let_discovery_decide(window) -> None:
    """`connect("")` probes the candidates; a literal "Auto" would be opened as
    a device path and fail."""
    from macrokey.ui.app import AUTO_PORT

    window.port_box.setCurrentText(AUTO_PORT)
    assert window._chosen_port() == ""
    window.port_box.setCurrentText("/dev/ttyACM1")
    assert window._chosen_port() == "/dev/ttyACM1"


def test_hold_is_not_offered_as_a_binding(window) -> None:
    """It is the recording trigger. A key bound to both would fire the binding
    at 400 ms and open the recorder at 3 s, from one press."""
    assert {gesture for _, gesture in window.buttons} == {"tap", "double"}


def test_the_keys_are_not_wrapped_in_tabs(window) -> None:
    from PySide6.QtWidgets import QTabWidget

    assert not window.findChildren(QTabWidget)


def test_storage_usage_is_shown_in_the_status_bar(window) -> None:
    """Always visible in the default window — not only while recording."""
    text = window.storage_label.text()
    assert "% used" in text
    assert "% free" in text
    assert "/" in text
    assert window.statusBar().isVisibleTo(window)
    window._refresh_storage()
    assert window.storage_label.text() == text

