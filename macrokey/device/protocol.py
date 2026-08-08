"""Codec for the line protocol in ``docs/PROTOCOL.md``.

Pure functions over strings: no serial port, no threads. That keeps the wire
format testable without hardware, which is most of why it is a separate module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1
LINE_MAX = 96


class ProtocolError(ValueError):
    """Raised for a malformed or unsupported line."""


@dataclass
class Message:
    verb: str
    sub: str | None = None
    args: dict[str, str] = field(default_factory=dict)
    id: str | None = None

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.args.get(key, default)

    def int(self, key: str, default: int | None = None) -> int | None:
        value = self.args.get(key)
        if value is None:
            return default
        try:
            return int(value, 10)
        except ValueError:
            return default


def parse(line: str) -> Message:
    """Parses one received line. Unknown keys are kept, not rejected."""
    tokens = line.strip().split()
    if not tokens:
        raise ProtocolError("empty line")

    message = Message(verb=tokens[0])
    for token in tokens[1:]:
        if "=" not in token:
            if message.sub is None:
                message.sub = token
            continue
        key, _, value = token.partition("=")
        if key == "id":
            message.id = value
        else:
            message.args[key] = value
    return message


def encode(verb: str, *positional: str, **args: Any) -> str:
    """Builds a line to send. ``None`` values are dropped."""
    parts = [verb]
    parts.extend(positional)
    for key, value in args.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = 1 if value else 0
        parts.append(f"{key}={value}")

    line = " ".join(parts)
    if len(line) > LINE_MAX:
        raise ProtocolError(f"line exceeds {LINE_MAX} bytes: {line[:40]}...")
    return line


def rgb_hex(color: tuple[int, int, int] | str) -> str:
    """Normalises a colour to the six uppercase hex digits the device expects."""
    if isinstance(color, str):
        text = color.strip().lstrip("#")
        if len(text) != 6:
            raise ProtocolError(f"colour must be RRGGBB, got {color!r}")
        int(text, 16)  # validates
        return text.upper()
    red, green, blue = (max(0, min(255, int(channel))) for channel in color)
    return f"{red:02X}{green:02X}{blue:02X}"


def frame_arg(colors: list[tuple[int, int, int]]) -> str:
    return ",".join(rgb_hex(color) for color in colors)


@dataclass
class Hello:
    protocol: int
    firmware: str
    board: str
    keys: int
    leds: int
    layers: int
    profile_bytes: int

    @classmethod
    def from_message(cls, message: Message) -> Hello:
        if message.verb != "HELLO":
            raise ProtocolError(f"expected HELLO, got {message.verb}")
        return cls(
            protocol=message.int("proto", 0) or 0,
            firmware=message.get("fw", "?") or "?",
            board=message.get("board", "?") or "?",
            keys=message.int("keys", 0) or 0,
            leds=message.int("leds", 0) or 0,
            layers=message.int("layers", 0) or 0,
            profile_bytes=message.int("bytes", 0) or 0,
        )

    @property
    def compatible(self) -> bool:
        return self.protocol == PROTOCOL_VERSION


@dataclass
class KeyEvent:
    """``EV t=key`` -- informational; the HID report already went out."""

    key: int
    gesture: str
    layer: int
    uptime_ms: int


@dataclass
class HostEvent:
    """``EV t=host`` -- the device did nothing and expects the app to act."""

    token: int
    key: int
    layer: int


@dataclass
class RecordRequest:
    """``EV t=record`` -- a key was held alone long enough to mean "program me".

    The device holds no recording state of its own. It reports the request and
    the host decides whether that starts or finishes a recording, which keeps
    the two from disagreeing about which mode they are in after a reconnect.
    """

    key: int
    uptime_ms: int = 0


@dataclass
class ChordEvent:
    keys: list[int]
    layer: int


def parse_event(message: Message) -> KeyEvent | HostEvent | ChordEvent | RecordRequest | None:
    if message.verb != "EV":
        return None
    kind = message.get("t")
    if kind == "key":
        return KeyEvent(
            key=message.int("k", -1) or -1,
            gesture=message.get("g", "?") or "?",
            layer=message.int("l", 0) or 0,
            uptime_ms=message.int("ms", 0) or 0,
        )
    if kind == "host":
        return HostEvent(
            # `tok`, not `id`: `id` is reserved for request/response correlation.
            token=message.int("tok", -1) or -1,
            key=message.int("k", -1) or -1,
            layer=message.int("l", 0) or 0,
        )
    if kind == "record":
        return RecordRequest(
            key=message.int("k", -1) or -1,
            uptime_ms=message.int("ms", 0) or 0,
        )
    if kind == "chord":
        mask_text = message.get("m", "0") or "0"
        try:
            mask = int(mask_text, 16)
        except ValueError:
            mask = 0
        return ChordEvent(
            keys=[bit for bit in range(8) if mask & (1 << bit)],
            layer=message.int("l", 0) or 0,
        )
    return None
