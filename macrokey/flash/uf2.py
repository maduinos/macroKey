"""Writing firmware to a board whose bootloader is a USB drive.

The RP2040 boot ROM appears as mass storage: dropping a `.uf2` on it programs
the flash and reboots the board. There is no protocol and no tool -- the whole
flasher is "find the volume, copy the file" -- which is why this is the method
worth preferring when a new board offers it.

Finding the volume is the only fiddly part, because where a removable drive is
mounted is a property of the desktop, not of the board.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import string
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ..boards import Board
from .errors import BootloaderTimeout, FlashError

#: Every UF2 bootloader drops this file on its volume. Matching on it as well
#: as on the label means a board whose label a desktop has renamed, or whose
#: label differs by revision, is still found.
INFO_FILE = "INFO_UF2.TXT"


def _mount_roots() -> list[Path]:
    """Directories removable volumes get mounted under on this desktop."""
    if sys.platform == "darwin":
        return [Path("/Volumes")]
    if sys.platform == "win32":
        return []  # drive letters, handled separately
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    roots = [Path("/media"), Path("/run/media"), Path("/mnt")]
    if user:
        roots = [Path("/media") / user, Path("/run/media") / user, *roots]
    return roots


def _windows_volumes() -> list[Path]:
    drives = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except (AttributeError, OSError):  # pragma: no cover - not Windows
        return drives
    for index, letter in enumerate(string.ascii_uppercase):
        if mask & (1 << index):
            drives.append(Path(f"{letter}:\\"))
    return drives


def _looks_like(path: Path, label: str) -> bool:
    """A mounted UF2 bootloader, not merely something with the right name.

    The name alone is not evidence. udisks creates the mount point under
    /media a moment before it mounts anything on it, and that bare directory
    belongs to root -- so a scan that trusted the label found `RPI-RP2`,
    declared the bootloader ready, and copied the image into a plain empty
    directory owned by someone else. Which fails with EACCES if you are lucky,
    and fills the disk while reporting success if you are not.

    `INFO_UF2.TXT` only exists once the board's own filesystem is really there,
    which makes it the thing worth checking. It is also why the label is not
    required: desktops rename mounts and revisions differ, but every UF2
    bootloader drops this file.
    """
    try:
        if not path.is_dir():
            return False
        return (path / INFO_FILE).is_file()
    except OSError:  # a volume can vanish mid-scan; that is not an error
        return False


def _writable(path: Path) -> bool:
    """Mounted is not the same as ready: udisks mounts read-only first."""
    return os.access(path, os.W_OK)


def find_volume(board: Board) -> Path | None:
    """The mounted bootloader volume for `board`, or None if it is not up."""
    label = board.bootloader_volume
    if not label:
        return None
    if sys.platform == "win32":
        for drive in _windows_volumes():
            if _looks_like(drive, label):
                return drive
        return None
    matches: list[Path] = []
    for root in _mount_roots():
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        matches.extend(child for child in children if _looks_like(child, label))
    if not matches:
        return None
    # Two UF2 boards can be plugged in at once. The label is what tells them
    # apart, so it decides here even though it does not decide what counts.
    for match in matches:
        if match.name.upper() == label.upper():
            return match
    return matches[0]


def wait_for_volume(
    board: Board,
    *,
    timeout: float = 30.0,
    poll: float = 0.4,
    status: Callable[[str], None] = lambda _message: None,
) -> Path:
    """Blocks until the bootloader volume is mounted.

    Polls rather than watching the desktop's mount events: those differ per
    platform and per session type, and the thing being waited for takes seconds
    at most.
    """
    said = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        volume = find_volume(board)
        # Writable, not merely present: the desktop can have the filesystem
        # mounted read-only for a moment while it finishes, and a copy started
        # in that window fails on a board that was about to be perfectly ready.
        if volume is not None and _writable(volume):
            return volume
        if not said:
            status(f"Waiting for the {board.bootloader_volume} drive")
            said = True
        time.sleep(poll)
    raise BootloaderTimeout(
        f"the {board.bootloader_volume} drive did not appear within {timeout:.0f}s"
    )


def write_image(
    image: Path, volume: Path, *, status: Callable[[str], None] = lambda _m: None
) -> None:
    """Copies `image` onto `volume`, which programs the board and reboots it.

    The board disconnects the moment it has the whole file, so the flush and
    fsync that would normally confirm a copy can themselves fail with the
    volume already gone. That is success, not failure -- but only after the
    bytes are all written, which is why the copy itself is not forgiving.
    """
    if not image.is_file():
        raise FlashError(f"firmware image is missing: {image}")
    target = volume / image.name
    status(f"Copying {image.name} to {volume}")
    try:
        with open(image, "rb") as source, open(target, "wb") as destination:
            shutil.copyfileobj(source, destination, length=64 * 1024)
            destination.flush()
            try:
                os.fsync(destination.fileno())
            except OSError:
                # The board rebooted as soon as it had the image. Expected.
                pass
    except OSError as exc:
        # Same story one level up: a write that fails *after* the last block
        # went out is the board leaving, and there is no way to tell the two
        # apart from here. Report it, and let the caller decide by looking for
        # the pad coming back.
        raise FlashError(f"could not write to {volume}: {exc}") from exc
    status("Image written; the board is restarting")
