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
from macrokey.ui.app import MainWindow  # noqa: E402


@pytest.fixture
def window(monkeypatch):
    QApplication.instance() or QApplication([])
    # Before the window exists: `__init__` arms the auto-connect on a zero
    # timer, so patching it afterwards is already too late. The timer fires
    # during whatever later test next lets Qt run, and a profile that does not
    # match the pad's opens a *modal* box there -- `_resolve_profile_mismatch`
    # calls `exec()`, and offscreen nobody can answer it. The suite then hangs
    # in a test that has nothing to do with the one that armed it.
    monkeypatch.setattr(MainWindow, "_autoconnect", lambda _self: None)
    # Same reason, same timing: both open a *modal* box -- `_maybe_fix_capture`
    # asks whether to enable recording, and offscreen nobody can answer either.
    monkeypatch.setattr(MainWindow, "_maybe_fix_capture", lambda _self, **_kw: None)
    monkeypatch.setattr(MainWindow, "_offer_flat_pointer_at_startup", lambda _self: None)
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


@pytest.fixture
def notices(window, monkeypatch):
    """What went into a message box, without one opening."""
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MainWindow, "_notify", lambda _self, title, text: seen.append((title, text))
    )
    return seen


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


def test_a_pad_that_is_current_is_not_offered_anything(
    window, bundled_firmware_image
) -> None:
    found = collect(window.firmwareUpdateFound)
    window._check_firmware_update(hello(bundled_firmware_image))
    assert found == []


def test_a_pad_that_is_behind_is_offered_the_bundled_image(
    window, bundled_firmware_image
) -> None:
    found = collect(window.firmwareUpdateFound)
    window._firmware_checked.clear()
    window._check_firmware_update(hello("0.0.1"))
    assert len(found) == 1
    board_id, running, version = found[0]
    assert board_id == DEFAULT_BOARD.id and running == "0.0.1"
    assert version == bundled_firmware_image


def test_the_same_version_is_only_looked_at_once_a_session(
    window, bundled_firmware_image
) -> None:
    """Reconnects happen on a timer; a flapping cable must not ask repeatedly."""
    found = collect(window.firmwareUpdateFound)
    window._firmware_checked.clear()
    window._check_firmware_update(hello("0.0.1"))
    window._check_firmware_update(hello("0.0.1"))
    assert len(found) == 1


def test_nothing_is_checked_while_the_setting_is_off(
    window, bundled_firmware_image
) -> None:
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


def test_a_write_that_did_not_happen_is_not_reported_as_one(window, notices) -> None:
    """`firmware.update` decides again what to write and can decide nothing.
    The link still has to come back, but "installed" about a write that never
    happened is a lie the status bar has no way to take back."""
    said = collect(window.statusMessage)
    window._firmware_update_finished("9.9.8", "")
    assert any("already current" in message for message in said)
    assert not any("updated to" in message for message in said)

    window._firmware_update_finished("9.9.8", "9.9.9")
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


# ------------------------------------------------ saying what an update did --
#
# Both updates install themselves with nobody watching. A version number alone
# leaves someone to work out why the app -- or the pad -- they did not choose to
# change now behaves differently, which is the shape of a false bug report.


def test_the_release_notes_go_in_the_box(window, notices) -> None:
    window._app_update_ready("9.9.9", "Fixed the **thing** that was `broken`.")

    _title, text = notices[0]
    assert "9.9.9" in text
    assert "Fixed the thing that was broken." in text, "markdown is punctuation here"


def test_the_file_listing_is_not_release_notes(window, notices) -> None:
    """The workflow writes the changelog, a rule, then what it uploaded. Only
    the half above the rule answers "what changed"."""
    window._app_update_ready(
        "9.9.9", "Made the pointer land where you drew it.\n\n---\n\n- macrokey.exe"
    )

    _title, text = notices[0]
    assert "Made the pointer land where you drew it." in text
    assert "macrokey.exe" not in text


def test_a_release_with_no_notes_still_says_it_installed(window, notices) -> None:
    window._app_update_ready("9.9.9", "")

    _title, text = notices[0]
    assert "9.9.9" in text
    assert "What changed" not in text, "an empty heading is worse than none"


def test_a_very_long_release_note_is_cut(window, notices) -> None:
    window._app_update_ready("9.9.9", "\n".join(f"line {n}" for n in range(500)))

    _title, text = notices[0]
    assert len(text) < app_module.RELEASE_SUMMARY_MAX_CHARS + 500
    assert "…" in text


def test_a_reflashed_pad_says_so_rather_than_only_mentioning_it(
    window, notices
) -> None:
    """It is triggered by plugging a cable in and takes the keypad away for a
    few seconds. A status bar line scrolls past; this must not."""
    window._firmware_update_finished("0.9.3", "0.9.4")

    title, text = notices[0]
    assert "firmware" in title.lower()
    assert "0.9.3" in text and "0.9.4" in text
    assert "macros were not touched" in text, "the first worry is the macros"


def test_nothing_is_announced_when_nothing_was_written(window, notices) -> None:
    window._firmware_update_finished("0.9.4", "")

    assert notices == []


def test_about_names_who_made_it_and_where_to_find_them(window, monkeypatch) -> None:
    """The one place the app points outside itself, so the address has to be
    both correct and clickable."""
    shown: list[str] = []
    monkeypatch.setattr(
        app_module.QMessageBox, "setText", lambda _self, text: shown.append(text)
    )
    monkeypatch.setattr(app_module.QMessageBox, "exec", lambda _self: 0)

    window._show_about()

    assert "maduinos" in shown[0]
    assert app_module.MADUINOS_URL in shown[0]
    assert f'href="{app_module.MADUINOS_URL}"' in shown[0], "it must be a link"
