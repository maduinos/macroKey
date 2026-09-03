"""Keeping the keypad's firmware level with the app's.

The app already carries an image for every board, so the ordinary update needs
no network at all: the pad says which version it is running in `HELLO`, the
image says which version it is, and if the image is newer it gets written.

The network is the second source, not the first. It covers the two cases the
bundled image cannot: a source checkout that has never built one, and a release
published after this app was installed. Anything fetched is checked against the
release's own SHA256SUMS before it is allowed near a bootloader.

Which image is newer is decided by reading the version *out of the images*
(`flash.images.image_version`) rather than by trusting a filename or a tag. A
firmware version and an app version are different numbers on different clocks,
and pretending otherwise is how a pad gets reflashed with what it already runs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from ..boards import Board, board_by_id
from ..config.store import config_dir
from ..flash import NoImage, find_image, flash
from ..flash.images import image_version
from . import releases
from .releases import UpdateError

log = logging.getLogger(__name__)

Status = Callable[[str], None]


def cache_dir() -> Path:
    """Where downloaded images are kept, next to the profile and settings."""
    return config_dir() / "firmware"


@dataclass(frozen=True)
class Candidate:
    """An image that could be written, and the version it would leave behind."""

    image: Path
    version: str | None
    #: True when it came off the network rather than out of this build.
    downloaded: bool = False

    def newer_than(self, running: str) -> bool:
        if self.version is None:
            # An image whose version cannot be read is never *offered* as an
            # update. It is still perfectly writable by hand -- `macrokey flash`
            # does not come through here -- but "update" has to mean something.
            return False
        return releases.is_newer(self.version, running)


def bundled(board: Board) -> Candidate | None:
    """The image that shipped inside this build, if there is one."""
    try:
        image = find_image(board)
    except NoImage:
        return None
    return Candidate(image=image, version=image_version(image))


def published(
    board: Board,
    release: releases.Release,
    *,
    status: Status = lambda _message: None,
) -> Candidate | None:
    """The image on `release`, downloaded and checked. None on failure.

    Kept per release tag so a second look at the same release is a no-op, and
    so a half-finished download from an interrupted run is never mistaken for a
    complete one -- `releases.download` renames into place only after the hash
    matches.
    """
    target = cache_dir() / release.tag / board.firmware_name
    if not target.is_file():
        try:
            releases.fetch_asset(
                release, board.firmware_name, target.parent, status=status
            )
        except UpdateError as exc:
            log.info("could not fetch %s: %s", board.firmware_name, exc)
            return None
    return Candidate(image=target, version=image_version(target), downloaded=True)


def best(
    board: Board,
    running: str,
    *,
    allow_network: bool = True,
    status: Status = lambda _message: None,
) -> Candidate | None:
    """The newest image worth writing to a pad running `running`, or None.

    The bundled image is consulted first and settles it whenever it is already
    newer than the pad: an update that needs no network is the one that works on
    the bench, on a metered connection, and behind a company proxy. The network
    is asked only when what is on hand cannot help.
    """
    local = bundled(board)
    if local is not None and local.newer_than(running):
        return local
    if not allow_network or not releases.enabled():
        return None
    try:
        release = releases.latest_release()
    except UpdateError as exc:
        log.info("could not read the latest release: %s", exc)
        return None
    # The firmware follows the app. When this app *is* the newest release, the
    # image it carries is the newest published one and there is nothing out
    # there to fetch -- without this the ordinary case, a pad that is already
    # current, downloaded a copy of what it was holding on every connect.
    if local is not None and not releases.is_newer(release.version, __version__):
        return None
    remote = published(board, release, status=status)
    if remote is None or not remote.newer_than(running):
        return None
    if local is not None and local.version and remote.version:
        if not releases.is_newer(remote.version, local.version):
            return None
    return remote


def update(
    board_id: str,
    running: str,
    *,
    allow_network: bool = True,
    status: Status = lambda _message: None,
) -> str | None:
    """Brings the attached pad up to the newest firmware. Returns that version.

    None when there was nothing to do, which is the ordinary answer and not a
    failure. Flashing problems are raised as `FlashError` for the caller to
    report, because a keypad left in its bootloader is not something to swallow.
    """
    board = board_by_id(board_id)
    if board is None:
        return None
    candidate = best(board, running, allow_network=allow_network, status=status)
    if candidate is None:
        return None
    status(
        f"Updating {board.display_name} firmware {running} -> {candidate.version}"
    )
    flash(board_id=board.id, image=candidate.image, status=status)
    return candidate.version
