"""Mouse actions and recording instructions in the one-slot editor."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from macrokey.app import MacroKeyApp  # noqa: E402
from macrokey.ui.slot_dialog import SlotDialog  # noqa: E402


@pytest.fixture
def app(monkeypatch, tmp_path):
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    instance = MacroKeyApp()
    yield instance
    instance.close()


@pytest.mark.parametrize(
    ("kind", "value", "field", "expected"),
    [
        ("mouse_button", "left", "button", "left"),
        ("mouse_button", "right", "button", "right"),
        ("mouse_wheel", 1, "delta", 1),
        ("mouse_wheel", -1, "delta", -1),
    ],
)
def test_a_reliable_current_pointer_action_can_be_assigned_directly(
    app, kind, value, field, expected
) -> None:
    dialog = SlotDialog(None, app, 0, "tap")
    dialog._use_mouse(kind, value)
    assert dialog.result_action.kind == kind
    assert getattr(dialog.result_action, field) == expected


def test_double_recording_explains_the_required_second_press(app) -> None:
    dialog = SlotDialog(None, app, 0, "double")
    text = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "250 ms" in text
    assert "press and hold" in text
    assert dialog.record_hint.minimumHeight() >= dialog.fontMetrics().lineSpacing() * 4


def test_clear_names_only_the_binding_it_will_clear(app) -> None:
    dialog = SlotDialog(None, app, 2, "double")
    labels = [button.text() for button in dialog.findChildren(QPushButton)]
    assert "Clear double binding" in labels
    assert "Clear this key" not in labels
