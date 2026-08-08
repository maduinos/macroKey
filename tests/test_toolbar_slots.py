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


CLICKED_SLOTS = ("_toggle_connection", "_brightness_settled", "_rescan_ports")


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
