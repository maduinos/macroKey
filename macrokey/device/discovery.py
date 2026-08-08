"""Finding the keypad among the serial ports on the machine."""

from __future__ import annotations

from dataclasses import dataclass

try:  # pyserial is optional so the UI and tests run without hardware installed.
    from serial.tools import list_ports
except ModuleNotFoundError:  # pragma: no cover - exercised only without pyserial
    list_ports = None

# Arduino LLC / Arduino SA vendor ids, plus SparkFun for the Pro Micro.
KNOWN_VENDORS = {0x2341, 0x2A03, 0x1B4F}
# Leonardo, Leonardo bootloader, Micro, Micro bootloader.
KNOWN_PRODUCTS = {0x8036, 0x0036, 0x8037, 0x0037}


@dataclass
class PortCandidate:
    device: str
    description: str
    vid: int | None
    pid: int | None

    @property
    def likely(self) -> bool:
        return self.vid in KNOWN_VENDORS or self.pid in KNOWN_PRODUCTS

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
