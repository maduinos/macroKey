"""The editor's half of updating: what it does on its own, and when it waits.

An update writes firmware to the keypad without anyone asking, so the tests
that matter are the ones about *not* doing it -- not while a recording is
running, not while the pad is being synced, and not twice for the same version.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import macrokey.ui.app as app_module  # noqa: E402
from macrokey.boards import DEFAULT_BOARD  # noqa: E402
from macrokey.flash.images import find_image, image_version  # noqa: E402
from macrokey.ui.app import MainWindow  # noqa: E402


@pytest.fixture
def window(monkeypatch):
    QApplication.instance() or QApplication([])
    made = MainWindow()
    # A real keypad on the machine running the tests must not be touched, and
    # the window auto-connects to one at startup. Left alone, that connect is
    # still in flight when a test runs -- `_connecting` stays true, and every
    # update decision here is deliberately postponed while it is.
    made._connection_timer.stop()
    monkeypatch.setattr(made, "_toggle_connection", lambda **_kwargs: None)
    made._connecting = False
    # A pad that went away mid-check is left alone, so these need one that has
    # not. Patched on the class: `connected` is a property.
    monkeypatch.setattr(
        type(made.app.device), "connected", property(lambda _self: True)
    )
    # Nothing in this file may reach a real bootloader. `firmwareUpdateFound`
    # is wired to the slot that starts a flash, and these tests emit it.
    made.flashed = []
    monkeypatch.setattr(made, "_update_firmware", lambda *args: made.flashed.append(args))
    return made


def collect(signal) -> list:
    """Records what a signal carries. Connected, because `emit` is read-only."""
    seen: list = []
    signal.connect(lambda *args: seen.append(args if len(args) != 1 else args[0]))
    return seen


def hello(version: str, board: str = DEFAULT_BOARD.id) -> SimpleNamespace:
    return SimpleNamespace(firmware=version, board=board)


def test_the_help_menu_offers_the_two_switches_and_a_manual_check(window) -> None:
    labels = {
        action.text()
        for menu in window.menuBar().findChildren(type(window.menuBar().addMenu("x")))
        for action in menu.actions()
    }
    assert "Check for updates" in labels
    assert "Update the app automatically" in labels
    assert "Update keypad firmware automatically" in labels


def test_turning_automatic_firmware_updates_off_sticks(window) -> None:
    window._auto_update_firmware_toggled(False)
    assert window.app.settings.auto_update_firmware is False
    from macrokey.config.store import Settings

    assert Settings.load().auto_update_firmware is False
    window._auto_update_firmware_toggled(True)


def test_a_pad_that_is_current_is_not_offered_anything(window, monkeypatch) -> None:
    found = collect(window.firmwareUpdateFound)
    running = image_version(find_image(DEFAULT_BOARD))
    assert running is not None
    window._check_firmware_update(hello(running))
    assert found == []


def test_a_pad_that_is_behind_is_offered_the_bundled_image(window, monkeypatch) -> None:
    found = collect(window.firmwareUpdateFound)
    window._firmware_checked.clear()
    window._check_firmware_update(hello("0.0.1"))
    assert len(found) == 1
    board_id, running, version = found[0]
    assert board_id == DEFAULT_BOARD.id and running == "0.0.1"
    assert version == image_version(find_image(DEFAULT_BOARD))


def test_the_same_version_is_only_looked_at_once_a_session(window, monkeypatch) -> None:
    """Reconnects happen on a timer; a flapping cable must not ask repeatedly."""
    found = collect(window.firmwareUpdateFound)
    window._firmware_checked.clear()
    window._check_firmware_update(hello("0.0.1"))
    window._check_firmware_update(hello("0.0.1"))
    assert len(found) == 1


def test_nothing_is_checked_while_the_setting_is_off(window, monkeypatch) -> None:
    found = collect(window.firmwareUpdateFound)
    window.app.settings.auto_update_firmware = False
    window._firmware_checked.clear()
    window._check_firmware_update(hello("0.0.1"))
    assert found == []
    # ...but asking for it explicitly still looks.
    window._check_firmware_update(hello("0.0.1"), asked_for=True)
    assert len(found) == 1
    window.app.settings.auto_update_firmware = True


def test_a_recording_is_never_interrupted_to_flash(window, monkeypatch) -> None:
    """The pad is gone for several seconds mid-flash, so it waits -- and looks
    again shortly rather than giving up, because a cable that stays put never
    produces a second connect to notice it on."""
    started = window.flashed
    looked_again: list[int] = []
    monkeypatch.setattr(
        "macrokey.ui.app.QTimer.singleShot",
        lambda ms, _slot: looked_again.append(ms),
    )
    monkeypatch.setattr(type(window.session), "recording", property(lambda _self: True))

    window._firmware_update_found(DEFAULT_BOARD.id, "0.0.1", "9.9.9")

    assert started == []
    assert looked_again == [app_module.FIRMWARE_UPDATE_RETRY_MS]


def test_a_pad_that_went_away_is_forgotten_not_retried(window, monkeypatch) -> None:
    """Retrying would end in a flash with no board to flash, which is a failure
    dialog for something that simply got unplugged."""
    monkeypatch.setattr(type(window.app.device), "connected", property(lambda _self: False))
    window._firmware_checked.add(f"{DEFAULT_BOARD.id}@0.0.1")

    window._firmware_update_found(DEFAULT_BOARD.id, "0.0.1", "9.9.9")

    assert window.flashed == []
    assert f"{DEFAULT_BOARD.id}@0.0.1" not in window._firmware_checked


def test_the_answer_arriving_during_the_connect_is_not_dropped(
    window, monkeypatch
) -> None:
    """The check runs on the connect worker, so `_connecting` is still true when
    its answer is delivered. Dropping it there meant a stable link -- one
    connect, ever -- never updated its firmware at all."""
    pending: list = []
    monkeypatch.setattr(
        "macrokey.ui.app.QTimer.singleShot", lambda _ms, slot: pending.append(slot)
    )
    window._connecting = True

    window._firmware_update_found(DEFAULT_BOARD.id, "0.0.1", "9.9.9")
    assert window.flashed == []

    window._connecting = False
    pending.pop()()
    assert window.flashed == [(DEFAULT_BOARD.id, "0.0.1", "9.9.9")]


def test_an_idle_pad_is_updated_without_being_asked(window, monkeypatch) -> None:
    started = window.flashed
    window.app.settings.auto_update_firmware = True
    window._firmware_update_found(DEFAULT_BOARD.id, "0.0.1", "9.9.9")
    assert started == [(DEFAULT_BOARD.id, "0.0.1", "9.9.9")]


def test_with_automatic_updates_off_it_asks_first(window, monkeypatch) -> None:
    asked: list[dict] = []
    started = window.flashed
    monkeypatch.setattr(window, "_ask", lambda **kwargs: asked.append(kwargs))
    window.app.settings.auto_update_firmware = False

    window._firmware_update_found(DEFAULT_BOARD.id, "0.0.1", "9.9.9")

    assert started == [], "an update nobody switched on must be a question first"
    assert len(asked) == 1
    asked[0]["then"]()
    assert started == [(DEFAULT_BOARD.id, "0.0.1", "9.9.9")]
    window.app.settings.auto_update_firmware = True


def test_a_source_checkout_says_so_rather_than_pretending_to_check(
    window, monkeypatch
) -> None:
    messages = collect(window.statusMessage)
    window._check_app_update(asked_for=True)
    assert any("git pull" in message for message in messages)


def test_a_write_that_did_not_happen_is_not_reported_as_one(window) -> None:
    """`firmware.update` decides again what to write and can decide nothing.
    The link still has to come back, but "installed" about a write that never
    happened is a lie the status bar has no way to take back."""
    said = collect(window.statusMessage)
    window._firmware_update_finished("")
    assert any("already current" in message for message in said)
    assert not any("updated to" in message for message in said)

    window._firmware_update_finished("9.9.9")
    assert any("updated to 9.9.9" in message for message in said)


def test_a_question_on_screen_holds_the_flash_off(window, monkeypatch) -> None:
    """"Which profile wins" is a question about this keypad. Taking the pad
    away underneath it leaves the answer to apply to a device that is gone."""
    pending: list = []
    monkeypatch.setattr(
        "macrokey.ui.app.QTimer.singleShot", lambda _ms, slot: pending.append(slot)
    )
    window._profile_prompt_open = True

    window._firmware_update_found(DEFAULT_BOARD.id, "0.0.1", "9.9.9")
    assert window.flashed == []

    window._profile_prompt_open = False
    pending.pop()()
    assert window.flashed == [(DEFAULT_BOARD.id, "0.0.1", "9.9.9")]
