"""Toolbar buttons, and the argument Qt quietly passes them.

`clicked` carries a `checked` boolean. A slot with an optional positional
parameter therefore receives False rather than its default, which is how
"Write to device" ended every write with "'bool' object is not callable".
Nothing failed at connect time and nothing failed on the click -- it broke at
the end, in a worker callback, which is the worst place to find out.
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def window():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    return MainWindow()


TOOLBAR_SLOTS = ("_save", "_push", "_pull", "_toggle_connection", "_brightness_settled")


@pytest.mark.parametrize("name", TOOLBAR_SLOTS)
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


def test_write_to_device_survives_being_clicked(window) -> None:
    button = next(b for b in window.device_buttons if b.text() == "Write to device")
    button.setEnabled(True)
    button.click()
    # The completion handler runs on the GUI thread after the worker finishes;
    # it used to raise here rather than at the click.
    window._on_push_finished()


def test_read_from_device_survives_being_clicked(window) -> None:
    button = next(b for b in window.device_buttons if b.text() == "Read from device")
    button.setEnabled(True)
    button.click()


def test_device_buttons_are_disabled_while_disconnected(window) -> None:
    window._refresh_connection()
    assert window.app.device.connected is False
    assert [b.isEnabled() for b in window.device_buttons] == [False, False]
