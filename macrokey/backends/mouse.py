"""Mouse output, for the recordings the firmware cannot replay by itself.

Buttons and wheel only, at wherever the pointer already is. Positions are
deliberately absent: a macro that moves the pointer to a remembered coordinate
is correct exactly until a window moves, a resolution changes or a second
monitor appears, and then it silently clicks something else. What survives is
"click here, now", which is also the part the keypad can send on its own.
"""

from __future__ import annotations

BUTTONS = ("left", "right", "middle")


class MouseError(RuntimeError):
    """Raised when mouse output is unavailable."""


class MouseBackend:
    def __init__(self) -> None:
        self._controller = None

    def available(self) -> tuple[bool, str]:
        try:
            from pynput import mouse  # noqa: F401
        except Exception as exc:  # noqa: BLE001 - no input backend on this box
            return False, f"pynput cannot drive the mouse here: {exc}"
        return True, ""

    def require(self) -> None:
        usable, reason = self.available()
        if not usable:
            raise MouseError(reason)
        if self._controller is None:
            from pynput import mouse

            self._controller = mouse.Controller()

    def click(self, button: str = "left", clicks: int = 1) -> None:
        self.require()
        from pynput import mouse

        name = button if button in BUTTONS else "left"
        self._controller.click(getattr(mouse.Button, name), max(1, min(8, clicks)))

    def scroll(self, delta: int) -> None:
        """Positive scrolls up, matching the recorder's sign convention."""
        self.require()
        self._controller.scroll(0, int(delta))


_backend: MouseBackend | None = None


def get_mouse_backend() -> MouseBackend:
    global _backend
    if _backend is None:
        _backend = MouseBackend()
    return _backend
