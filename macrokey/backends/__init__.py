"""OS-specific capabilities, selected at runtime.

The original app imported ``win32clipboard`` at module scope, so every feature
died on anything but Windows. Here each capability reports its own availability
and a missing one fails just the action that needs it.
"""

from .clipboard import ClipboardBackend, get_clipboard_backend
from .keyboard import KeyboardBackend, get_keyboard_backend
from .window import active_window_title, window_backend_name

__all__ = [
    "ClipboardBackend",
    "KeyboardBackend",
    "active_window_title",
    "get_clipboard_backend",
    "get_keyboard_backend",
    "window_backend_name",
]
