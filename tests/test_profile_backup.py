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
    save_profile,
)


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
