"""The packaged GUI gets the same always-on diagnostics as the CLI."""

from __future__ import annotations

import logging

from macrokey.logging_setup import setup_logging


def test_logging_setup_is_idempotent_and_private(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    root = logging.getLogger()
    before = list(root.handlers)
    before_level = root.level
    try:
        setup_logging()
        setup_logging()
        handlers = [
            handler
            for handler in root.handlers
            if getattr(handler, "_macrokey_console", False)
            or getattr(handler, "_macrokey_file", False)
        ]
        assert sum(bool(getattr(h, "_macrokey_console", False)) for h in handlers) == 1
        assert sum(bool(getattr(h, "_macrokey_file", False)) for h in handlers) == 1

        logging.getLogger("macrokey.test").debug("standalone GUI diagnostic")
        for handler in handlers:
            handler.flush()
        log = tmp_path / "macrokey.log"
        assert "standalone GUI diagnostic" in log.read_text(encoding="utf-8")
        assert log.stat().st_mode & 0o077 == 0

        file_handler = next(h for h in handlers if getattr(h, "_macrokey_file", False))
        file_handler.doRollover()
        logging.getLogger("macrokey.test").debug("after rollover")
        file_handler.flush()
        assert log.stat().st_mode & 0o077 == 0
        assert (tmp_path / "macrokey.log.1").stat().st_mode & 0o077 == 0
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(before_level)
