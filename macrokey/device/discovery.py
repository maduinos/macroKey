"""Finding the keypad among the serial ports on the machine."""

from __future__ import annotations

from dataclasses import dataclass

from ..boards import Board, board_by_usb_id, in_bootloader, known_usb_ids

try:  # pyserial is optional so the UI and tests run without hardware installed.
    from serial.tools import list_ports
except ModuleNotFoundError:  # pragma: no cover - exercised only without pyserial
    list_ports = None

#: Every id any registered board can appear as. Derived, so adding a board to
#: `macrokey.boards` is all it takes to be recognised on the port list.
KNOWN_USB_IDS = known_usb_ids()


@dataclass
class PortCandidate:
    device: str
    description: str
    vid: int | None
    pid: int | None

    @property
    def board(self) -> Board | None:
        """Which registered board this is, by USB id alone.

        Known before anything is opened, and true of a board running no macroKey
        firmware at all -- which is what lets the app offer to flash one.
        """
        return board_by_usb_id(self.vid, self.pid)

    @property
    def likely(self) -> bool:
        # Product ids are vendor-scoped. Matching either half made an unrelated
        # board with a coincidentally equal PID look like macroKey and delayed or
        # prevented probing the generic clone that really was the keypad.
        return self.board is not None

    @property
    def in_bootloader(self) -> bool:
        """Sitting in its bootloader, so it answers a flasher and not IDENT."""
        board = self.board
        return board is not None and in_bootloader(board, self.vid, self.pid)

    def __str__(self) -> str:
        return f"{self.device} ({self.description})"


def candidates() -> list[PortCandidate]:
    """Serial ports worth trying, most likely first.

    Ordering matters more than filtering among USB ports: a board with a generic
    USB-serial bridge still answers IDENT, so an unrecognised vendor is tried
    last rather than excluded.

    Ports with no vendor id are dropped, though. On a PC those are the legacy
    /dev/ttyS0..31, and there are thirty-two of them: probing each one meant a
    page of "Could not configure port" for every auto-connect, and two seconds
    of settle time for any that did open. The keypad is a USB device, so it
    always has a vendor id.
    """
    if list_ports is None:
        return []
    found = [
        PortCandidate(
            device=port.device,
            description=port.description or "",
            vid=port.vid,
            pid=port.pid,
        )
        for port in list_ports.comports()
        if port.vid is not None
    ]
    found.sort(key=lambda candidate: (not candidate.likely, candidate.device))
    return found


def pyserial_available() -> bool:
    return list_ports is not None
