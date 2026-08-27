"""Settings writes are private, atomic, and safe across UI/worker threads."""

from __future__ import annotations

import json
import threading

from macrokey.config.store import Settings, settings_path


def test_settings_are_replaced_atomically_and_owner_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    Settings(port="/dev/one").save()

    path = settings_path()
    assert json.loads(path.read_text(encoding="utf-8"))["port"] == "/dev/one"
    assert path.stat().st_mode & 0o077 == 0
    assert list(tmp_path.glob(".settings.json.*.tmp")) == []


def test_concurrent_settings_saves_never_share_a_temporary_file(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    start = threading.Barrier(3)
    errors: list[Exception] = []

    def save(port: str) -> None:
        try:
            start.wait()
            Settings(port=port).save()
        except Exception as exc:  # noqa: BLE001 - failures are asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=save, args=("/dev/one",)),
        threading.Thread(target=save, args=("/dev/two",)),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()

    assert errors == []
    assert json.loads(settings_path().read_text(encoding="utf-8"))["port"] in {
        "/dev/one",
        "/dev/two",
    }


def test_valid_json_with_wrong_setting_types_falls_back_safely(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    settings_path().write_text(
        json.dumps(
            {
                "port": [],
                "recorder_min_gap_ms": -1,
                "recorder_capture_mouse": "yes",
                "recorder_anchor_mouse": True,
            }
        ),
        encoding="utf-8",
    )

    loaded = Settings.load()

    assert loaded.port == ""
    assert loaded.recorder_min_gap_ms == 40
    assert loaded.recorder_capture_mouse is False
    assert loaded.recorder_anchor_mouse is True


def test_non_object_settings_json_uses_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    settings_path().write_text("[]", encoding="utf-8")
    assert Settings.load() == Settings()
