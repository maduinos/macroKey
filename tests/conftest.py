"""The UI tests build real widgets, so Qt needs a platform it can use headless."""

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Point the config at a scratch directory before anything imports the store.
#
# These tests build real `MainWindow`s, and a real window loads and saves real
# `Settings`. Left alone they read and write the config of whoever is running
# them: the pointer-acceleration tests exercise the "no thanks" path, which
# persists `pointer_accel_declined`, and that setting is remembered forever --
# so running the suite silently turned off an offer the developer had never
# been shown, on their own machine. The port, the language and the profile are
# all reachable the same way.
#
# Set here rather than in a fixture because module-level imports and
# session-scoped windows can both read a path before any fixture runs.
_CONFIG_DIR = tempfile.mkdtemp(prefix="macrokey-tests-")
os.environ["MACROKEY_CONFIG_DIR"] = _CONFIG_DIR

# No test reaches the internet. The update checks run from a startup timer and
# from every successful connect, so without this a suite run on a machine with
# a keypad attached would ask GitHub about it -- and a suite run without one
# would depend on the network to pass. The tests that cover updating stub the
# release layer directly and lift this for themselves.
os.environ["MACROKEY_NO_UPDATE"] = "1"

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


@pytest.fixture(scope="session", autouse=True)
def _config_is_a_scratch_directory():
    """Fails loudly if anything reaches around the override above."""
    from macrokey.config import store

    assert str(store.config_dir()) == _CONFIG_DIR, (
        f"the tests are pointed at {store.config_dir()}, not the scratch "
        "directory -- they would edit a real config"
    )
    yield
