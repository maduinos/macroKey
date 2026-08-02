"""Driving the WS2812 strip from host-side state."""

from .palette import RECORDING_SCENE, STATE_SCENES, LedScene, scene_for
from .service import LedService
from .source import ActivityEvent, ActivityEventServer, default_socket_path

__all__ = [
    "RECORDING_SCENE",
    "STATE_SCENES",
    "ActivityEvent",
    "ActivityEventServer",
    "LedScene",
    "LedService",
    "default_socket_path",
    "scene_for",
]
