"""Raw capture events, before any interpretation."""

from __future__ import annotations

from dataclasses import dataclass, field

KEY_DOWN = "key_down"
KEY_UP = "key_up"
MOUSE_CLICK = "mouse_click"
#: The button coming back up. Kept as its own event rather than folded into the
#: press, because a press and a release with movement between them is a drag,
#: and collapsing the pair into one click is what made drags unrecordable.
MOUSE_RELEASE = "mouse_release"
SCROLL = "scroll"
#: Pointer motion, as a relative (dx, dy) in `data`, already accumulated by the
#: capture source. Never absolute: a macro that replays coordinates clicks the
#: right pixel only until a window moves or a second monitor appears.
MOUSE_MOVE = "mouse_move"


@dataclass(frozen=True)
class RawEvent:
    kind: str
    #: Normalised name in macroKey's hotkey vocabulary: "ctrl", "a", "enter".
    token: str
    #: The printable character this produced, if any. Differs from `token` for
    #: shifted keys, which is why both are kept.
    char: str = ""
    #: Seconds from an arbitrary origin (``time.monotonic``).
    at: float = 0.0
    data: tuple[int, ...] = field(default_factory=tuple)

    @property
    def printable(self) -> bool:
        return bool(self.char) and self.char.isprintable() and len(self.char) == 1
