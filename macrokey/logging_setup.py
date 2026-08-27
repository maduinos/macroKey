"""Logging shared by the CLI and the standalone GUI launcher."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(verbose: bool = False) -> None:
    """Keep a diagnostic log without duplicating handlers on repeated setup.

    Recordings are often made while the window is not being watched. The file
    is therefore always detailed; the terminal stays quiet unless requested.
    PyInstaller launches ``main.py`` directly, so this cannot live only in the
    CLI entry point.
    """
    from .config.store import _restrict, profile_path

    class PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
        """Re-applies 0600 whenever rotation creates a fresh log file."""

        def _open(self):
            stream = super()._open()
            _restrict(Path(self.baseFilename))
            return stream

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console = next(
        (handler for handler in root.handlers if getattr(handler, "_macrokey_console", False)),
        None,
    )
    if console is None:
        console = logging.StreamHandler()
        console._macrokey_console = True  # type: ignore[attr-defined]
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)

    if any(getattr(handler, "_macrokey_file", False) for handler in root.handlers):
        return
    try:
        path = profile_path().parent / "macrokey.log"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        to_file = PrivateRotatingFileHandler(
            path, maxBytes=512_000, backupCount=1, encoding="utf-8"
        )
        to_file._macrokey_file = True  # type: ignore[attr-defined]
        to_file.setLevel(logging.DEBUG)
        to_file.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(to_file)
        _restrict(path)
        _restrict(path.parent, directory=True)
    except OSError:
        pass  # an unwritable diagnostic log must not prevent the app opening
