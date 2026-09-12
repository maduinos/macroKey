"""Updating the app itself, in place, from its own releases.

Only a frozen build can do this. A source checkout is a git working tree and
updating it is `git pull` -- overwriting somebody's checkout with a binary would
be a surprising thing for a keypad editor to do, so it is refused with a
sentence saying what to run instead.

The download never lands on the running file. It is staged under a name of its
own beside it, because a release binary is normally run under the name it was
published as -- and downloading "the new version" straight onto that path is
writing over the program doing the writing.

The swap is then a rename, not a copy over the running file:

* Linux replaces the path while the old inode stays open. The running process
  keeps working off the file it started from and the next launch gets the new
  one.
* Windows will not let the running .exe be *replaced*, but it will let it be
  *renamed*. So the old binary is moved aside to `.old` and the new one takes
  the name; the leftover is deleted at the next start, by which time nothing
  has it open.

Neither path restarts anything. The user is told a restart is what applies it,
because an editor that vanishes and reappears while a keypad is mid-recording
is worse than a one-line message.
"""

from __future__ import annotations

import logging
import os
import platform
import stat
import sys
from collections.abc import Callable
from pathlib import Path

from .. import __version__
from ..runtime import frozen
from . import releases
from .releases import Release, UpdateError

log = logging.getLogger(__name__)

Status = Callable[[str], None]

#: Suffix the outgoing binary is parked under on Windows, and cleaned up from
#: at the next start. Also used on Linux, where it is merely tidy.
RETIRED_SUFFIX = ".old"


def asset_name() -> str | None:
    """The release asset this platform runs, or None if none is published.

    Deliberately narrow. The workflow builds x86-64 for Linux and Windows and
    nothing else; guessing a name for an arm64 mac would produce a 404 at best
    and the wrong binary at worst.
    """
    machine = platform.machine().lower()
    if machine not in ("x86_64", "amd64"):
        return None
    if sys.platform.startswith("win"):
        return "macrokey-windows-x86_64.exe"
    if sys.platform.startswith("linux"):
        return "macrokey-linux-x86_64"
    return None


def executable() -> Path:
    """The file that would be replaced -- the frozen binary, not the loader."""
    return Path(sys.executable).resolve()


def supported() -> tuple[bool, str]:
    """Whether this build can update itself, and why not when it cannot."""
    if not frozen():
        return False, "this is a source checkout -- update it with git pull"
    if asset_name() is None:
        return False, f"no release is published for {sys.platform} {platform.machine()}"
    if not releases.enabled():
        return False, "updates are switched off (MACROKEY_NO_UPDATE)"
    return True, ""


def check() -> Release | None:
    """The published release, if it is newer than what is running.

    Raises `UpdateError` when the question could not be asked at all, so a
    caller that a person is watching can say "could not check" rather than
    "you are up to date", which is a different and much worse answer.
    """
    release = releases.latest_release()
    if not releases.is_newer(release.version, __version__):
        return None
    return release


def cleanup(directory: Path | None = None) -> None:
    """Deletes a previous update's retired binary. Never raises.

    Called at startup. On Windows the file cannot be removed at the moment it
    is replaced -- it is still the image of the running process -- so it is
    removed the next time round, when it is nobody's.
    """
    if not frozen():
        return
    target = (directory or executable().parent) / (executable().name + RETIRED_SUFFIX)
    try:
        if target.exists():
            target.unlink()
            log.info("removed the previous binary at %s", target)
    except OSError as exc:
        log.info("could not remove %s: %s", target, exc)


def apply(
    release: Release,
    *,
    status: Status = lambda _message: None,
    target: Path | None = None,
) -> Path:
    """Downloads `release` and puts it in place of the running binary.

    Returns the path that now holds the new version. Raises `UpdateError` and
    changes nothing if the download, the checksum, or the swap fails -- the
    running binary is only ever moved *after* a verified replacement is sitting
    complete on the same filesystem.
    """
    name = asset_name()
    if name is None:
        raise UpdateError(f"no release binary for {sys.platform} {platform.machine()}")
    current = target or executable()
    staged = current.with_name(f".{current.name}.new")
    staged.unlink(missing_ok=True)
    log.info(
        "updating %s to v%s from asset %s, staging at %s",
        current, release.version, name, staged,
    )

    # Downloaded *as* the staging name, not as the asset's own name. Someone who
    # downloads a release binary runs it under the name it was published as, so
    # `current.parent / name` is the running program -- and the download used to
    # land on top of it. Windows refuses to replace a running .exe, so the
    # update failed there every time for anyone who had not renamed the file.
    try:
        downloaded = releases.fetch_asset(
            release, name, current.parent, filename=staged.name, status=status
        )
    except OSError as exc:
        # `download` turns its own OSErrors into UpdateError, so this is for the
        # ones raised before it gets that far -- an unwritable directory is the
        # ordinary case. The contract above says UpdateError, and the caller
        # catches exactly that; anything else reached the UI as a bare traceback.
        staged.unlink(missing_ok=True)
        raise UpdateError(f"could not download {name}: {exc}") from exc
    try:
        # The archive bit is not carried by an HTTPS body, so a downloaded
        # Linux binary arrives unrunnable unless this is done.
        staged.chmod(staged.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        staged.unlink(missing_ok=True)
        raise UpdateError(f"could not prepare {staged}: {exc}") from exc
    assert downloaded == staged, "the download did not land where it was staged"

    retired = current.with_name(current.name + RETIRED_SUFFIX)
    status(f"Installing v{release.version}")
    try:
        retired.unlink(missing_ok=True)
        if current.exists():
            os.replace(current, retired)
        os.replace(staged, current)
    except OSError as exc:
        # Put back whatever was moved, so a failure here leaves a working app.
        log.warning("could not install %s over %s: %s", staged, current, exc)
        if not current.exists() and retired.exists():
            try:
                os.replace(retired, current)
                log.warning("restored %s from %s", current, retired)
            except OSError:
                log.error("could not restore %s from %s", current, retired)
        staged.unlink(missing_ok=True)
        raise UpdateError(f"could not replace {current}: {exc}") from exc

    if not sys.platform.startswith("win"):
        # Windows still has the old image open; that copy goes at the next start.
        try:
            retired.unlink(missing_ok=True)
        except OSError:
            pass
    log.info("installed v%s at %s", release.version, current)
    return current
