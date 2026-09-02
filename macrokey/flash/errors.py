"""What flashing can fail with, separated by what the caller should do about it."""

from __future__ import annotations


class FlashError(Exception):
    """Flashing failed and there is nothing useful to retry."""


class NoImage(FlashError):
    """No firmware image for this board is available to write."""


class NeedsManualBootloader(FlashError):
    """The board has to be put into its bootloader by hand.

    Not a failure of the flasher: a board that has never run macroKey firmware
    has no code listening for a request to reboot, so nothing in software can
    do this. `hint` is what the person should physically do, and the caller is
    expected to show it and then wait rather than give up.
    """

    def __init__(self, hint: str, message: str = "") -> None:
        super().__init__(message or hint)
        self.hint = hint


class BootloaderTimeout(FlashError):
    """The bootloader was asked for, or awaited, and never appeared."""
