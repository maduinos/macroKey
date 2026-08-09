"""Serial link to the keypad."""

from .client import DeviceClient, DeviceError
from .discovery import PortCandidate, candidates, pyserial_available
from .protocol import Hello, HostEvent, KeyEvent, RecordRequest

__all__ = [
    "RecordRequest",
    "DeviceClient",
    "DeviceError",
    "Hello",
    "HostEvent",
    "KeyEvent",
    "PortCandidate",
    "candidates",
    "pyserial_available",
]
