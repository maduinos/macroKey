"""Finding the keypad among the serial ports on the machine."""

from __future__ import annotations

from dataclasses import dataclass

try:  # pyserial is optional so the UI and tests run without hardware installed.
    from serial.tools import list_ports
except ModuleNotFoundError:  # pragma: no cover - exercised only without pyserial
    list_ports = None

# Leonardo, Leonardo bootloader, Micro, Micro bootloader.
ARDUINO_PRODUCTS = {0x8036, 0x0036, 0x8037, 0x0037}
# SparkFun Pro Micro bootloader/sketch ids (3.3 V and 5 V variants). The shipped
# hardware is the 5 V pair 9205/9206, but recognising both does not exclude it
# during a board swap or bootloader window.
SPARKFUN_PRODUCTS = {0x9203, 0x9204, 0x9205, 0x9206}
KNOWN_USB_IDS = {
    *((vendor, product) for vendor in (0x2341, 0x2A03) for product in ARDUINO_PRODUCTS),
    *((0x1B4F, product) for product in SPARKFUN_PRODUCTS),
}


@dataclass
class PortCandidate:
    device: str
    description: str
    vid: int | None
    pid: int | None

    @property
    def likely(self) -> bool:
        # Product ids are vendor-scoped. Matching either half made an unrelated
        # board with a coincidentally equal PID look like macroKey and delayed or
        # prevented probing the generic clone that really was the keypad.
        return (self.vid, self.pid) in KNOWN_USB_IDS

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
