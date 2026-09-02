"""Putting firmware on a board, without asking anyone to install a toolchain.

The images are built by CI and travel with the app, so flashing is a file copy
(UF2 boards) or a small serial protocol (AVR109 boards) -- no arduino-cli, no
avrdude, no cores, no libraries.
"""

from __future__ import annotations

from .attached import Found, detect, find_board
from .errors import (
    BootloaderTimeout,
    FlashError,
    NeedsManualBootloader,
    NoImage,
)
from .images import available, find_image
from .service import enter_bootloader, flash, wait_for_pad, write

__all__ = [
    "BootloaderTimeout",
    "FlashError",
    "Found",
    "NeedsManualBootloader",
    "NoImage",
    "available",
    "detect",
    "enter_bootloader",
    "find_board",
    "find_image",
    "flash",
    "wait_for_pad",
    "write",
]
