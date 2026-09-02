"""Getting firmware onto whatever is plugged in.

The whole point is that someone who has just soldered a keypad plugs it in,
answers one question, and has a working pad. So this decides for itself which
board it is, which image it needs, and how to get the board into its bootloader
-- and asks only where a person genuinely has to act.

There is exactly one such place. A board that has never run macroKey firmware
cannot be asked to reboot into its bootloader, because nothing is listening.
That first time needs a button or a jumper, and no amount of software removes
it. Every flash after it is automatic.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from ..boards import Board
from . import avr109, uf2
from .attached import Found, detect, find_board
from .errors import BootloaderTimeout, FlashError, NeedsManualBootloader
from .images import find_image

Status = Callable[[str], None]

#: How long to wait for a bootloader to come up after asking for it. Caterina
#: appears in about a second; the RP2040's drive has to be mounted by the
#: desktop, which is the slower half.
BOOTLOADER_TIMEOUT = 30.0


def _touch_1200(port: str) -> None:
    """Opens and closes a port at 1200 baud, which is the reboot request.

    Both cores watch for this: the Arduino IDE has used it to reset boards for
    upload for years, so a board running any normal sketch honours it. macroKey
    firmware also answers `BOOT`, but this works on a board running something
    else entirely, which is the case that matters here.
    """
    try:
        import serial
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise FlashError("pyserial is not installed: pip install pyserial") from exc
    try:
        handle = serial.Serial(port, 1200)
        handle.dtr = False
        handle.close()
    except Exception as exc:  # noqa: BLE001 - the port vanishing is normal here
        # The board can reset before close() returns, which surfaces as an
        # error on a port that is doing exactly what was asked.
        if not isinstance(exc, OSError):
            raise FlashError(f"could not reset {port}: {exc}") from exc


def _wait_for_bootloader_port(board: Board, *, timeout: float, status: Status) -> str:
    deadline = time.monotonic() + timeout
    said = False
    while time.monotonic() < deadline:
        for item in detect():
            if item.board.id == board.id and item.bootloader and item.port:
                return item.port
        if not said:
            status(f"Waiting for the {board.display_name} bootloader")
            said = True
        time.sleep(0.3)
    raise BootloaderTimeout(
        f"the {board.display_name} bootloader did not appear within {timeout:.0f}s"
    )


def enter_bootloader(found: Found, *, timeout: float = BOOTLOADER_TIMEOUT, status: Status) -> Found:
    """Returns a `Found` that is ready to be written to.

    Already in a bootloader: nothing to do. Running: ask it to reboot, then
    wait for the bootloader to show up as whatever it shows up as -- a port for
    AVR109, a mounted drive for UF2. Neither: the board has to be put there by
    hand, and `NeedsManualBootloader` carries the sentence to show.
    """
    board = found.board
    if found.ready_to_flash:
        return found

    if found.port is None:
        raise NeedsManualBootloader(board.first_flash_hint)

    status(f"Asking {board.display_name} to restart into its bootloader")
    _touch_1200(found.port)

    if board.flash_method == "uf2":
        volume = uf2.wait_for_volume(board, timeout=timeout, status=status)
        return Found(board=board, volume=volume, bootloader=True)
    port = _wait_for_bootloader_port(board, timeout=timeout, status=status)
    return Found(board=board, port=port, bootloader=True)


def write(found: Found, image: Path, *, status: Status) -> None:
    """Writes `image` to a board that is already in its bootloader."""
    board = found.board
    if board.flash_method == "uf2":
        if found.volume is None:
            raise FlashError(f"{board.display_name} is not mounted as a drive")
        uf2.write_image(image, found.volume, status=status)
    elif board.flash_method == "avr109":
        if found.port is None:
            raise FlashError(f"{board.display_name} has no bootloader port")
        avr109.write_image(image, found.port, status=status)
    else:  # pragma: no cover - the registry refuses to build such a board
        raise FlashError(f"no flasher for method {board.flash_method!r}")


def wait_for_pad(board: Board, *, timeout: float = 20.0, status: Status) -> str | None:
    """Waits for the freshly flashed board to come back as a serial port.

    Best effort. A board that does not reappear is worth saying so about, but
    it is not evidence the write failed -- on Linux the port also has to be
    readable by this user, which is a separate problem with its own advice.
    """
    status("Waiting for the keypad to come back")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for item in detect():
            if item.board.id == board.id and not item.bootloader and item.port:
                return item.port
        time.sleep(0.4)
    return None


def flash(
    *,
    board_id: str | None = None,
    image: Path | None = None,
    status: Status = lambda _message: None,
    timeout: float = BOOTLOADER_TIMEOUT,
) -> str | None:
    """Detect, flash, and wait for the pad. Returns the port it came back on.

    Raises `NeedsManualBootloader` when a person has to act; the caller shows
    the hint and calls again once they have.
    """
    found = find_board(board_id)
    if found is None:
        raise FlashError(
            "no board found. Plug the keypad in, or hold its bootloader button "
            "while plugging it in if it has never been flashed."
        )
    status(f"Found {found}")

    picked = image or find_image(found.board)
    ready = enter_bootloader(found, timeout=timeout, status=status)
    write(ready, picked, status=status)
    return wait_for_pad(found.board, status=status)
