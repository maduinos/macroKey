"""Profile model.

These dataclasses are the host-side mirror of the firmware's EEPROM layout.
The constants below must match ``arduino/macrokey/src/Config.h``; ``binary.py``
depends on both agreeing and the device rejects a blob of the wrong size.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from . import keycodes

KEY_COUNT = 8
LED_COUNT = 8
LAYER_COUNT = 4
CHORD_SLOTS = 8
MACRO_SLOTS = 16
MACRO_STEP_CAPACITY = 149  # (480 - 32 index bytes) // 3 bytes per step

GESTURES = ("tap", "double", "hold")

SCHEMA_VERSION = 1

# Action type ids, shared with ActionTypes.h.
ACTION_TYPE_IDS: dict[str, int] = {
    "none": 0,
    "key": 1,
    "consumer": 2,
    "mouse_button": 3,
    "mouse_move": 4,
    "mouse_wheel": 5,
    "layer_momentary": 6,
    "layer_toggle": 7,
    "sequence": 8,
    "host": 9,
    "led_scene": 10,
    "delay": 11,
}

KEYF_REPEAT = 0x01
KEYF_STICKY = 0x02


class ProfileError(ValueError):
    """Raised when a profile cannot be represented on the device."""


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@dataclass(frozen=True)
class Action:
    """One slot's behaviour.

    A single wide struct rather than a class hierarchy: every field is optional,
    JSON round-trips without custom decoders, and a UI can bind straight to it.
    Only fields relevant to ``kind`` are read.
    """

    kind: str = "none"
    hotkey: str = ""       # kind="key"
    usage: str = ""        # kind="consumer"
    button: str = "left"   # kind="mouse_button"
    clicks: int = 1
    dx: int = 0            # kind="mouse_move"
    dy: int = 0
    delta: int = 0         # kind="mouse_wheel"
    layer: int = 0         # kind="layer_momentary" / "layer_toggle"
    slot: int = 0          # kind="sequence"
    token: int = 0         # kind="host"
    scene: int = 0         # kind="led_scene"
    delay_ms: int = 0      # kind="delay", macro steps only
    repeat: bool = False
    sticky: bool = False

    def __post_init__(self) -> None:
        if self.kind not in ACTION_TYPE_IDS:
            raise ProfileError(f"unknown action kind: {self.kind!r}")

    @property
    def is_empty(self) -> bool:
        return self.kind == "none"

    def encode(self) -> tuple[int, int, int, int]:
        """Packs into the four bytes the firmware stores per slot."""
        type_id = ACTION_TYPE_IDS[self.kind]
        if self.kind == "key":
            modifiers, code = keycodes.parse_hotkey(self.hotkey)
            flags = (KEYF_REPEAT if self.repeat else 0) | (KEYF_STICKY if self.sticky else 0)
            return type_id, modifiers, code, flags
        if self.kind == "consumer":
            usage = keycodes.CONSUMER_USAGES.get(self.usage)
            if usage is None:
                raise ProfileError(f"unknown consumer usage: {self.usage!r}")
            return type_id, usage & 0xFF, (usage >> 8) & 0xFF, 0
        if self.kind == "mouse_button":
            mask = keycodes.MOUSE_BUTTONS.get(self.button)
            if mask is None:
                raise ProfileError(f"unknown mouse button: {self.button!r}")
            return type_id, mask, _clamp(self.clicks, 1, 5), 0
        if self.kind == "mouse_move":
            return type_id, _clamp(self.dx, -127, 127) & 0xFF, _clamp(self.dy, -127, 127) & 0xFF, 0
        if self.kind == "mouse_wheel":
            return type_id, _clamp(self.delta, -127, 127) & 0xFF, 0, 0
        if self.kind in ("layer_momentary", "layer_toggle"):
            return type_id, _clamp(self.layer, 0, LAYER_COUNT - 1), 0, 0
        if self.kind == "sequence":
            return type_id, _clamp(self.slot, 0, MACRO_SLOTS - 1), 0, 0
        if self.kind == "host":
            return type_id, _clamp(self.token, 0, 255), 0, 0
        if self.kind == "led_scene":
            return type_id, _clamp(self.scene, 0, 255), 0, 0
        if self.kind == "delay":
            return type_id, _clamp(round(self.delay_ms / 10), 0, 255), 0, 0
        return 0, 0, 0, 0

    @classmethod
    def decode(cls, type_id: int, a: int, b: int, c: int) -> Action:
        kind = ID_TO_KIND.get(type_id, "none")
        if kind == "key":
            return cls(
                kind="key",
                hotkey=keycodes.format_hotkey(a, b),
                repeat=bool(c & KEYF_REPEAT),
                sticky=bool(c & KEYF_STICKY),
            )
        if kind == "consumer":
            usage = a | (b << 8)
            name = next((k for k, v in keycodes.CONSUMER_USAGES.items() if v == usage), "")
            return cls(kind="consumer", usage=name)
        if kind == "mouse_button":
            name = next((k for k, v in keycodes.MOUSE_BUTTONS.items() if v == a), "left")
            return cls(kind="mouse_button", button=name, clicks=max(1, b))
        if kind == "mouse_move":
            return cls(kind="mouse_move", dx=_signed(a), dy=_signed(b))
        if kind == "mouse_wheel":
            return cls(kind="mouse_wheel", delta=_signed(a))
        if kind in ("layer_momentary", "layer_toggle"):
            return cls(kind=kind, layer=a)
        if kind == "sequence":
            return cls(kind="sequence", slot=a)
        if kind == "host":
            return cls(kind="host", token=a)
        if kind == "led_scene":
            return cls(kind="led_scene", scene=a)
        if kind == "delay":
            return cls(kind="delay", delay_ms=a * 10)
        return cls()

    def describe(self) -> str:
        """Human-readable summary, shown before a recording is saved."""
        if self.kind == "key":
            suffix = " (repeat)" if self.repeat else " (sticky)" if self.sticky else ""
            return f"{self.hotkey}{suffix}"
        if self.kind == "consumer":
            return f"media: {self.usage}"
        if self.kind == "mouse_button":
            return f"mouse {self.button} x{self.clicks}"
        if self.kind == "mouse_move":
            return f"mouse move {self.dx:+d},{self.dy:+d}"
        if self.kind == "mouse_wheel":
            return f"wheel {self.delta:+d}"
        if self.kind == "layer_momentary":
            return f"layer {self.layer} while held"
        if self.kind == "layer_toggle":
            return f"toggle layer {self.layer}"
        if self.kind == "sequence":
            return f"device macro #{self.slot}"
        if self.kind == "host":
            return f"host action #{self.token}"
        if self.kind == "led_scene":
            return f"led scene {self.scene}"
        if self.kind == "delay":
            return f"wait {self.delay_ms} ms"
        return "-"

    def to_dict(self) -> dict[str, Any]:
        """Emits only the fields that differ from the defaults."""
        if self.is_empty:
            return {"kind": "none"}
        blank = Action(kind=self.kind)
        data: dict[str, Any] = {"kind": self.kind}
        for name in self.__dataclass_fields__:
            if name == "kind":
                continue
            value = getattr(self, name)
            if value != getattr(blank, name):
                data[name] = value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Action:
        if not data:
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


ID_TO_KIND = {value: key for key, value in ACTION_TYPE_IDS.items()}


def _signed(value: int) -> int:
    return value - 256 if value > 127 else value


@dataclass
class KeySlot:
    tap: Action = field(default_factory=Action)
    double: Action = field(default_factory=Action)
    hold: Action = field(default_factory=Action)
    color: str = "001014"

    def gesture(self, name: str) -> Action:
        return getattr(self, name)

    def with_gesture(self, name: str, action: Action) -> KeySlot:
        return replace(self, **{name: action})

    def to_dict(self) -> dict[str, Any]:
        return {
            "tap": self.tap.to_dict(),
            "double": self.double.to_dict(),
            "hold": self.hold.to_dict(),
            "color": self.color,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeySlot:
        return cls(
            tap=Action.from_dict(data.get("tap")),
            double=Action.from_dict(data.get("double")),
            hold=Action.from_dict(data.get("hold")),
            color=str(data.get("color", "001014")),
        )


@dataclass
class Layer:
    name: str = ""
    keys: list[KeySlot] = field(default_factory=lambda: [KeySlot() for _ in range(KEY_COUNT)])

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "keys": [key.to_dict() for key in self.keys]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Layer:
        keys = [KeySlot.from_dict(item) for item in data.get("keys", [])]
        keys = (keys + [KeySlot() for _ in range(KEY_COUNT)])[:KEY_COUNT]
        return cls(name=str(data.get("name", "")), keys=keys)


@dataclass
class Chord:
    keys: list[int] = field(default_factory=list)
    action: Action = field(default_factory=Action)

    @property
    def mask(self) -> int:
        mask = 0
        for index in self.keys:
            if 0 <= index < KEY_COUNT:
                mask |= 1 << index
        return mask

    def to_dict(self) -> dict[str, Any]:
        return {"keys": list(self.keys), "action": self.action.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chord:
        return cls(
            keys=[int(value) for value in data.get("keys", [])],
            action=Action.from_dict(data.get("action")),
        )


@dataclass
class HostAction:
    """A step executed by the desktop app rather than the firmware."""

    type: str = "noop"
    name: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "name": self.name, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HostAction:
        return cls(
            type=str(data.get("type", "noop")),
            name=str(data.get("name", "")),
            params=dict(data.get("params", {})),
        )

    def describe(self) -> str:
        return self.name or self.type


@dataclass
class Profile:
    schema_version: int = SCHEMA_VERSION
    name: str = "default"
    brightness: int = 64
    base_layer: int = 0
    layers: list[Layer] = field(default_factory=list)
    chords: list[Chord] = field(default_factory=list)
    # Token -> action run on the PC. Keys are ints, matching `EV t=host id=`.
    host_actions: dict[int, HostAction] = field(default_factory=dict)
    device_macros: list[list[Action]] = field(default_factory=list)

    def __post_init__(self) -> None:
        while len(self.layers) < LAYER_COUNT:
            self.layers.append(Layer(name=f"Layer {len(self.layers)}"))
        del self.layers[LAYER_COUNT:]

    def slot(self, layer: int, key: int) -> KeySlot:
        return self.layers[layer].keys[key]

    def action(self, layer: int, key: int, gesture: str) -> Action:
        return self.slot(layer, key).gesture(gesture)

    def set_action(self, layer: int, key: int, gesture: str, action: Action) -> None:
        self.layers[layer].keys[key] = self.slot(layer, key).with_gesture(gesture, action)

    def next_host_token(self) -> int:
        """Lowest unused token. 200+ is reserved for chords and system hooks."""
        for token in range(200):
            if token not in self.host_actions:
                return token
        raise ProfileError("no free host action token")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "brightness": self.brightness,
            "base_layer": self.base_layer,
            "layers": [layer.to_dict() for layer in self.layers],
            "chords": [chord.to_dict() for chord in self.chords],
            "host_actions": {
                str(token): action.to_dict() for token, action in sorted(self.host_actions.items())
            },
            "device_macros": [
                [step.to_dict() for step in macro] for macro in self.device_macros
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            name=str(data.get("name", "default")),
            brightness=_clamp(int(data.get("brightness", 64)), 0, 255),
            base_layer=_clamp(int(data.get("base_layer", 0)), 0, LAYER_COUNT - 1),
            layers=[Layer.from_dict(item) for item in data.get("layers", [])],
            chords=[Chord.from_dict(item) for item in data.get("chords", [])],
            host_actions={
                int(token): HostAction.from_dict(value)
                for token, value in data.get("host_actions", {}).items()
            },
            device_macros=[
                [Action.from_dict(step) for step in macro]
                for macro in data.get("device_macros", [])
            ],
        )


LAYER_COLORS = ("001014", "1c0c00", "12001c", "001806")


def default_profile() -> Profile:
    """Matches ``Profile::writeDefaults`` in the firmware.

    A freshly flashed board and a freshly installed app must agree, otherwise
    the first connection reports a spurious mismatch.
    """
    profile = Profile(name="default", brightness=64, base_layer=0)
    profile.layers = [
        Layer(name=name, keys=[KeySlot(color=LAYER_COLORS[index]) for _ in range(KEY_COUNT)])
        for index, name in enumerate(("Base", "Media", "Layer 2", "Layer 3"))
    ]

    # Layer 0: hyper + 1..8. Nothing binds ctrl+alt+shift+digit, so the pad is
    # useful immediately without stealing an existing shortcut.
    for key in range(KEY_COUNT):
        profile.set_action(0, key, "tap", Action(kind="key", hotkey=f"ctrl+alt+shift+{key + 1}"))
    profile.set_action(0, KEY_COUNT - 1, "hold", Action(kind="layer_momentary", layer=1))

    media = ("volume_down", "volume_up", "mute", "play_pause")
    for key, usage in enumerate(media):
        profile.set_action(1, key, "tap", Action(kind="consumer", usage=usage))
    for key in range(4, KEY_COUNT - 1):
        profile.set_action(1, key, "tap", Action(kind="host", token=key - 4))

    profile.chords = [Chord(keys=[0, 1], action=Action(kind="host", token=200))]
    profile.host_actions = {
        200: HostAction(type="stop", name="Stop running host action"),
    }
    return profile
