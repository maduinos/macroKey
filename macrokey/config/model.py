"""Profile model.

These dataclasses are the host-side mirror of the firmware's EEPROM layout.
The constants below must match ``firmware/src/Config.h``; ``binary.py``
depends on both agreeing and the device rejects a blob of the wrong size.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any

from . import keycodes

KEY_COUNT = 8
# One WS2812B on the pad. Kept separate from KEY_COUNT because the two are
# genuinely independent: the palette is per pixel, the keymap is per key.
LED_COUNT = 1
# No layers. Eight keys that each do one thing is the product; layers added a
# mode that was invisible and a way in that had to be remembered, and the keymap
# they cost is EEPROM that macro records use instead. What was left afterwards
# was a `layer` argument on almost every call that was always 0.
MACRO_SLOTS = 16
#: Three-byte records the macro region holds across every slot: the ATmega32u4's
#: whole 1 KB EEPROM, less the header, keymap and palette, less one count byte
#: per slot. `binary` asserts it against the layout it actually builds.
#:
#: A *record* is not a step. A typed run costs one header record plus one record
#: per three characters, so 308 records is roughly 900 characters of text -- the
#: old layout stored one 3 byte key action per character and managed 273.
MACRO_RECORD_CAPACITY = 308
#: The most records any one macro may use: a slot's count is a single byte.
MACRO_MAX_RECORDS = 255
#: The most characters one text record run may carry, for the same reason.
TEXT_RUN_MAX = 255

GESTURES = ("tap", "double", "hold")

#: What the editor offers. Hold is missing on purpose: holding a key alone for
#: three seconds is how recording starts and stops, and the firmware reports the
#: hold gesture on its way there. A key with both would fire its hold binding at
#: 400 ms and then open the recorder at 3 s -- one press, two unrelated things,
#: neither of them asked for. Hold belongs to recording; tap and double are the
#: two the pad can bind without ambiguity.
EDITABLE_GESTURES = ("tap", "double")

SCHEMA_VERSION = 2

#: What the pixel rests at, as RRGGBB. Must match the firmware's writeDefaults.
#: A dim blue-grey rather than off, because off reads as unplugged.
DEFAULT_RESTING_COLOR = "3c5073"  # (60, 80, 115)

# Action type ids, shared with ActionTypes.h.
ACTION_TYPE_IDS: dict[str, int] = {
    "none": 0,
    "key": 1,
    "consumer": 2,
    "mouse_button": 3,
    "mouse_move": 4,
    "mouse_wheel": 5,
    # 6 and 7 were layer_momentary and layer_toggle. The numbers stay retired
    # rather than being reused: an id is a wire format, and shuffling the ones
    # above them would turn every stored macro into a different macro.
    "sequence": 8,
    "host": 9,
    "led_scene": 10,
    "delay": 11,
    #: Macro records only. Carries its characters in the records that follow it
    #: rather than one 3 byte key action each, which is what makes a recording
    #: of real typed text fit on the pad at all.
    "text": 12,
    #: Drives the pointer into the top-left corner, so the moves after it are
    #: measured from a known origin. Macro records only.
    "mouse_home": 13,
}

KEYF_REPEAT = 0x01
KEYF_STICKY = 0x02

#: `Action.mode` for kind="mouse_button", stored in the record's `b` byte.
#: A press that is never released is what a drag is made of, so the button is
#: not implicitly let go the way a click does it.
MOUSE_MODES: dict[str, int] = {"click": 0, "press": 1, "release": 2}
ID_TO_MOUSE_MODE = {value: key for key, value in MOUSE_MODES.items()}


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
    mode: str = "click"    # click, press or release -- press is half a drag
    text: str = ""         # kind="text", macro records only
    dx: int = 0            # kind="mouse_move"
    dy: int = 0
    delta: int = 0         # kind="mouse_wheel"
    slot: int = 0          # kind="sequence"
    token: int = 0         # kind="host"
    scene: int = 0         # kind="led_scene"
    delay_ms: int = 0      # kind="delay", macro steps only
    repeat: bool = False
    sticky: bool = False

    def __post_init__(self) -> None:
        if self.kind not in ACTION_TYPE_IDS:
            raise ProfileError(f"unknown action kind: {self.kind!r}")
        if self.kind == "mouse_button" and self.mode not in MOUSE_MODES:
            raise ProfileError(f"unknown mouse mode: {self.mode!r}")
        if self.kind == "text":
            if not self.text:
                raise ProfileError("a text action needs text")
            if len(self.text) > TEXT_RUN_MAX:
                raise ProfileError(
                    f"a text run is at most {TEXT_RUN_MAX} characters, got {len(self.text)}"
                )
            if not self.text.isascii() or not all(0x20 <= ord(c) <= 0x7E for c in self.text):
                raise ProfileError("device text must be printable ASCII")

    @property
    def is_empty(self) -> bool:
        return self.kind == "none"

    # ----------------------------------------------------------- records --

    def records(self) -> list[tuple[int, int, int]]:
        """The 3-byte macro records this action occupies, in order.

        Almost every action is one record. Text is the exception: a header
        carrying the length, then its characters packed three to a record. That
        is the whole reason a recording of typed text fits -- as one key action
        per character it cost three bytes a letter and a single sentence filled
        the pad.
        """
        if self.kind == "text":
            payload = self.text.encode("ascii")
            records = [(ACTION_TYPE_IDS["text"], len(payload), 0)]
            for offset in range(0, len(payload), 3):
                chunk = payload[offset : offset + 3].ljust(3, b"\0")
                records.append((chunk[0], chunk[1], chunk[2]))
            return records
        type_id, a, b, _flags = self.encode()
        return [(type_id, a, b)]

    def record_count(self) -> int:
        """How many records `records()` will produce, without building them."""
        if self.kind == "text":
            return 1 + (len(self.text) + 2) // 3
        return 1

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
            return type_id, mask, MOUSE_MODES[self.mode], 0
        if self.kind == "mouse_home":
            return type_id, 0, 0, 0
        if self.kind == "text":
            # The header only. `records()` is what emits the characters, and a
            # text action cannot be bound to a key -- it exists inside macros.
            return type_id, len(self.text), 0, 0
        if self.kind == "mouse_move":
            return type_id, _clamp(self.dx, -127, 127) & 0xFF, _clamp(self.dy, -127, 127) & 0xFF, 0
        if self.kind == "mouse_wheel":
            return type_id, _clamp(self.delta, -127, 127) & 0xFF, 0, 0
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
            return cls(kind="mouse_button", button=name, mode=ID_TO_MOUSE_MODE.get(b, "click"))
        if kind == "mouse_move":
            return cls(kind="mouse_move", dx=_signed(a), dy=_signed(b))
        if kind == "mouse_wheel":
            return cls(kind="mouse_wheel", delta=_signed(a))
        if kind == "sequence":
            return cls(kind="sequence", slot=a)
        if kind == "host":
            return cls(kind="host", token=a)
        if kind == "led_scene":
            return cls(kind="led_scene", scene=a)
        if kind == "mouse_home":
            return cls(kind="mouse_home")
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
            verb = {"click": "click", "press": "hold down", "release": "let go of"}[self.mode]
            return f"{verb} {self.button} mouse button"
        if self.kind == "text":
            preview = self.text if len(self.text) <= 30 else self.text[:27] + "..."
            return f"type {preview!r}"
        if self.kind == "mouse_move":
            return f"mouse move {self.dx:+d},{self.dy:+d}"
        if self.kind == "mouse_wheel":
            return f"wheel {self.delta:+d}"
        if self.kind == "sequence":
            return f"device macro #{self.slot}"
        if self.kind == "host":
            return f"host action #{self.token}"
        if self.kind == "led_scene":
            return f"led scene {self.scene}"
        if self.kind == "mouse_home":
            return "move the pointer to the top-left corner"
        if self.kind == "delay":
            return f"wait {self.delay_ms} ms"
        return "-"

    def to_dict(self) -> dict[str, Any]:
        """Emits only the fields that differ from the defaults.

        The defaults are read off the dataclass rather than by building an
        instance to compare against. Building one meant constructing an action
        that was deliberately incomplete -- `Action(kind="text")` carries no
        text -- and `__post_init__` refuses exactly that. So saving any
        recording that contained typed words raised "a text action needs text"
        from the one method whose job is to write it down.
        """
        if self.is_empty:
            return {"kind": "none"}
        data: dict[str, Any] = {"kind": self.kind}
        for field_ in fields(self):
            if field_.name == "kind":
                continue
            value = getattr(self, field_.name)
            if value != field_.default:
                data[field_.name] = value
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

    def gesture(self, name: str) -> Action:
        return getattr(self, name)

    def with_gesture(self, name: str, action: Action) -> KeySlot:
        return replace(self, **{name: action})

    def to_dict(self) -> dict[str, Any]:
        return {
            "tap": self.tap.to_dict(),
            "double": self.double.to_dict(),
            "hold": self.hold.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeySlot:
        return cls(
            tap=Action.from_dict(data.get("tap")),
            double=Action.from_dict(data.get("double")),
            hold=Action.from_dict(data.get("hold")),
        )


def macro_records(macro: list[Action]) -> int:
    """How many 3-byte records a compiled macro occupies on the device.

    Not ``len(macro)``: a text action spans a header plus its packed
    characters, and it is records, not actions, that the region runs out of.
    """
    return sum(action.record_count() for action in macro)


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
    #: What the pixel rests at when nothing is happening, as RRGGBB.
    resting_color: str = DEFAULT_RESTING_COLOR
    keys: list[KeySlot] = field(default_factory=list)
    # No chords. They were eight EEPROM slots the editor never offered a way to
    # fill and the defaults left empty, so the region only ever held zeroes --
    # forty bytes that macro records now use instead.
    # Token -> action run on the PC. Keys are ints, matching `EV t=host id=`.
    host_actions: dict[int, HostAction] = field(default_factory=dict)
    device_macros: list[list[Action]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.keys = (self.keys + [KeySlot() for _ in range(KEY_COUNT)])[:KEY_COUNT]
        self._drop_reserved_bindings()

    def _drop_reserved_bindings(self) -> None:
        """Clears bindings this build can no longer honour.

        Any binding on hold, which is how recording starts. The editor stopped
        offering it and `set_action` refuses it, but profiles written before
        either still carry one, and a hold binding fires at 400 ms on the way to
        the recorder -- so it has to be cleared on the way in, not merely hidden.

        Layer switches used to be cleared here too. Those action ids are retired
        now, so they decode as nothing and need no special case.
        """
        reserved = [gesture for gesture in GESTURES if gesture not in EDITABLE_GESTURES]
        for index, slot in enumerate(self.keys):
            for gesture in reserved:
                if not slot.gesture(gesture).is_empty:
                    slot = slot.with_gesture(gesture, Action())
            self.keys[index] = slot

    def slot(self, key: int) -> KeySlot:
        # Checked rather than indexed straight through. A negative key is a
        # perfectly good Python index and means "counting from the end", so a
        # key that arrived as -1 read and wrote the *last* key without anything
        # going wrong anywhere -- the recording simply appeared on key 8. An
        # index this far off is a bug upstream, and it should say so here.
        if not 0 <= key < len(self.keys):
            raise ProfileError(f"key {key} is out of range (0..{len(self.keys) - 1})")
        return self.keys[key]

    def action(self, key: int, gesture: str) -> Action:
        return self.slot(key).gesture(gesture)

    def set_action(self, key: int, gesture: str, action: Action) -> None:
        # Refused here, not merely hidden in the editor. Hold is how recording
        # starts, and the firmware still reports the gesture at 400 ms on the
        # way there -- a binding on it would fire then, and the recorder would
        # open at 3 s: one press, two unrelated things. Clearing it in
        # ``__post_init__`` only catches it on the next load, by which time the
        # binding has already been written to the pad.
        if gesture not in EDITABLE_GESTURES and not action.is_empty:
            raise ProfileError(
                f"{gesture!r} is reserved: holding a key is how recording starts. "
                f"Bind one of {', '.join(EDITABLE_GESTURES)} instead."
            )
        self.keys[key] = self.slot(key).with_gesture(gesture, action)  # validates both

    def next_host_token(self) -> int:
        """Lowest unused token. 200+ is reserved for system hooks."""
        for token in range(200):
            if token not in self.host_actions:
                return token
        raise ProfileError("no free host action token")

    # ------------------------------------------------------------- storage --

    def referenced_storage(self) -> tuple[set[int], set[int]]:
        """``(macro slots, host tokens)`` some binding still points at."""
        slots: set[int] = set()
        tokens: set[int] = set()
        for key in self.keys:
            for gesture in GESTURES:
                action = key.gesture(gesture)
                if action.kind == "sequence":
                    slots.add(action.slot)
                elif action.kind == "host":
                    tokens.add(action.token)
        return slots, tokens

    def reclaim_storage(self) -> tuple[int, int]:
        """Empties macro slots and host actions nothing points at any more.

        Recording into a key that already held one is the ordinary case -- it is
        how a macro gets corrected -- and every time it happened the old slot was
        left full. Sixteen corrections to a single key filled all sixteen slots
        with unreachable steps, and from then on every recording fell back to a
        host action: the pad quietly stopped working with the app closed, which
        is the one thing it exists to do.

        Sweeping by reference rather than freeing the slot being replaced is
        what also repairs a profile that already leaked, and it stays correct if
        two bindings ever share a slot.

        Returns ``(slots freed, tokens freed)``.
        """
        slots, tokens = self.referenced_storage()

        freed_slots = 0
        for index, macro in enumerate(self.device_macros):
            if macro and index not in slots:
                self.device_macros[index] = []
                freed_slots += 1

        stale = [token for token in self.host_actions if token not in tokens]
        for token in stale:
            del self.host_actions[token]

        return freed_slots, len(stale)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "brightness": self.brightness,
            "resting_color": self.resting_color,
            "keys": [key.to_dict() for key in self.keys],
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
            resting_color=str(data.get("resting_color", DEFAULT_RESTING_COLOR)),
            keys=_keys_from_dict(data),
            host_actions={
                int(token): HostAction.from_dict(value)
                for token, value in data.get("host_actions", {}).items()
            },
            device_macros=[
                [Action.from_dict(step) for step in macro]
                for macro in data.get("device_macros", [])
            ],
        )


def _keys_from_dict(data: dict[str, Any]) -> list[KeySlot]:
    """Reads the key list, accepting the shape profiles used to be written in.

    They held a `layers` list, and everything lived in the first one. Rather
    than a migration step that has to be remembered, the old shape is simply
    still readable -- there was only ever one layer with anything in it.
    """
    if "keys" in data:
        return [KeySlot.from_dict(item) for item in data.get("keys", [])]
    layers = data.get("layers") or []
    if layers and isinstance(layers[0], dict):
        return [KeySlot.from_dict(item) for item in layers[0].get("keys", [])]
    return []


def default_profile() -> Profile:
    """Matches ``Profile::writeDefaults`` in the firmware.

    A freshly flashed board and a freshly installed app must agree, otherwise
    the first connection reports a spurious mismatch.
    """
    profile = Profile(name="default", brightness=64)

    # Hyper + 1..8. Nothing binds ctrl+alt+shift+digit, so the pad does
    # something useful the moment it is plugged in without taking a shortcut
    # away from anything already running.
    for key in range(KEY_COUNT):
        profile.set_action(key, "tap", Action(kind="key", hotkey=f"ctrl+alt+shift+{key + 1}"))

    # No host actions by default. The tokens that used to be here pointed at
    # nothing, so three keys did nothing at all.
    profile.host_actions = {}
    return profile
