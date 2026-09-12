"""Logging shared by the CLI and the standalone GUI launcher."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

log = logging.getLogger(__name__)

#: Name of the diagnostic log inside the config directory.
LOG_NAME = "macrokey.log"


def log_path() -> Path:
    """Where the diagnostic log lives, whether or not it could be opened.

    A separate function because it is also an answer someone needs: on Windows
    this is under %APPDATA% and nobody finds it by looking. The Help menu opens
    the folder it names.
    """
    from .config.store import profile_path

    return profile_path().parent / LOG_NAME


def setup_logging(verbose: bool = False) -> None:
    """Keep a diagnostic log without duplicating handlers on repeated setup.

    Recordings are often made while the window is not being watched. The file
    is therefore always detailed; the terminal stays quiet unless requested.
    PyInstaller launches ``main.py`` directly, so this cannot live only in the
    CLI entry point.
    """
    from .config.store import _restrict

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
        path = log_path()
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
        # First line of every run, so a log someone sends back says which file
        # it is and a log that is missing can be looked for in the right place.
        log.info("logging to %s", path)
    except OSError as exc:
        # An unwritable diagnostic log must not prevent the app opening -- but
        # it must not be silent either, or the answer to "there are no logs" is
        # unobtainable from the one place that knows. The console handler is
        # already attached, so this is seen with -v and in a terminal run.
        log.warning("could not open the log file at %s: %s", log_path(), exc)
