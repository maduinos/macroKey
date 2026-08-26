"""The UI tests build real widgets, so Qt needs a platform it can use headless."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _destroy_widgets_before_exit():
    """Tears the windows down while Qt is still standing.

    A window left alive at the end of the run is destroyed by Python's shutdown
    GC instead, which happens after Qt has dismantled the QApplication -- and
    PySide6 segfaults walking what is left of it. The run reported every test
    passed and the process still exited 139, which CI reads as a failed build.

    Session scope, not per-test: some UI tests share one window across a module
    and closing it after the first would leave the rest with a dead widget.
    """
    yield
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.processEvents()
