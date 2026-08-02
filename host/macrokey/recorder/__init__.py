"""Recording real input and turning it into assignable actions."""

from .events import RawEvent
from .normalize import normalize, reduce_to_device_action, summarize
from .recorder import Recorder, RecorderError

__all__ = [
    "RawEvent",
    "Recorder",
    "RecorderError",
    "normalize",
    "reduce_to_device_action",
    "summarize",
]
