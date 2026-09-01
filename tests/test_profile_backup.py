"""Local profile recovery and portable import/export."""

from __future__ import annotations

import json

import pytest

from macrokey.config import (
    Action,
    default_profile,
    export_profile,
    load_profile_file,
    profile_backup_path,
    profile_backup_paths,
    save_profile,
)
from macrokey.config.store import PROFILE_BACKUP_GENERATIONS


def test_every_save_keeps_the_previous_valid_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    before = default_profile()
    before.set_action(0, "tap", Action(kind="key", hotkey="a"))
    save_profile(before)

    after = default_profile()
    after.set_action(0, "tap", Action(kind="key", hotkey="b"))
    save_profile(after)

    restored = load_profile_file(profile_backup_path())
    assert restored.action(0, "tap").hotkey == "a"
    assert profile_backup_path().stat().st_mode & 0o077 == 0


def test_export_round_trips_without_changing_the_config_path(monkeypatch, tmp_path) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(config))
    profile = default_profile()
    profile.set_action(3, "double", Action(kind="key", hotkey="ctrl+s"))
    destination = tmp_path / "portable.json"

    export_profile(profile, destination)
    loaded = load_profile_file(destination)

    assert loaded.action(3, "double").hotkey == "ctrl+s"
    assert not (config / "profile.json").exists()
    assert json.loads(destination.read_text(encoding="utf-8"))["schema_version"] >= 1


def test_import_rejects_valid_json_that_is_not_a_profile(tmp_path) -> None:
    source = tmp_path / "not-a-profile.json"
    source.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_profile_file(source)


# --------------------------------------------------------- kept generations --
#
# One backup was not a recovery path for the case it exists for. What happened:
# a keypad that had lost its own profile was adopted onto the computer, which
# correctly pushed the still-good profile into the single backup slot -- and the
# next ordinary save, seventeen seconds later, pushed it straight back out. By
# the time anyone noticed the macros were gone, both copies were the empty one.


def _with_macro(hotkey: str) -> Profile:  # noqa: F821 - annotation only
    profile = default_profile()
    profile.set_action(0, "double", Action(kind="key", hotkey=hotkey))
    return profile


def test_a_good_profile_survives_later_saves_of_an_empty_one(monkeypatch, tmp_path) -> None:
    """The exact sequence that lost the macros, now recoverable."""
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))

    save_profile(_with_macro("ctrl+alt+t"))  # the work
    save_profile(default_profile())  # adopting the emptied keypad
    save_profile(default_profile())  # any ordinary save right after

    kept = [load_profile_file(path) for path in profile_backup_paths()]
    assert any(profile.action(0, "double").hotkey == "ctrl+alt+t" for profile in kept)


def test_backups_are_kept_newest_first(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    for index in range(4):
        save_profile(_with_macro(f"ctrl+{index}"))

    kept = [load_profile_file(path) for path in profile_backup_paths()]

    # The newest backup is the state before the last save, and so on back.
    assert [profile.action(0, "double").hotkey for profile in kept] == [
        "ctrl+2",
        "ctrl+1",
        "ctrl+0",
    ]


def test_the_rotation_is_bounded(monkeypatch, tmp_path) -> None:
    """Otherwise a long editing session fills the config directory."""
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    for index in range(PROFILE_BACKUP_GENERATIONS + 6):
        save_profile(_with_macro(f"ctrl+{index}"))

    assert len(profile_backup_paths()) == PROFILE_BACKUP_GENERATIONS


def test_every_kept_generation_stays_unreadable_to_other_accounts(
    monkeypatch, tmp_path
) -> None:
    """A backup holds whatever a recording captured, same as the profile."""
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    for index in range(3):
        save_profile(_with_macro(f"ctrl+{index}"))

    assert [path.stat().st_mode & 0o077 for path in profile_backup_paths()] == [0, 0]
