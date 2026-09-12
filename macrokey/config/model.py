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

#: Milliseconds the pad waits between characters of a typed run. 0 means "use
#: whatever the firmware was built with" (MK_MACRO_TEXT_DELAY_MS, 5 ms).
#:
#: Replay is faster than the recording was, and this is the knob. Consecutive
#: characters are merged into one text run with no timing kept -- reproducing
#: human typing speed is almost never wanted -- so the pad retypes them at a
#: fixed rate. That is too fast for anything that has to catch up: a terminal
#: still starting, a field that validates as you type.
DEFAULT_TEXT_SPEED_MS = 0
#: What a stored 0 actually does, and what the editor shows in its place.
#: Must match MK_MACRO_TEXT_DELAY_MS in firmware/src/Config.h.
FIRMWARE_TEXT_SPEED_MS = 5
#: The editor offers no 0. It used to, spelled "default", and stepping up from
#: it gave 1 ms -- five times *faster* than the default it had just left, with a
#: tooltip saying the opposite. So the knob now starts at 1 ms and the pad's own
#: rate is shown as the number it is.
MIN_TEXT_SPEED_MS = 1
#: The pad stores this in one byte.
MAX_TEXT_SPEED_MS = 255


def text_speed_shown(stored: int) -> int:
    """The stored value as the editor spells it: 0 is the firmware's 5 ms."""
    return stored or FIRMWARE_TEXT_SPEED_MS


def text_speed_stored(shown: int) -> int:
    """The editor's number as the pad stores it.

    5 ms goes back as 0, not as 5. A freshly flashed pad has 0 in that byte
    (``Profile::writeDefaults``) and the app compares profiles as raw bytes, so
    writing 5 for the same behaviour would report a difference that isn't one --
    a "Profile differs" prompt on the first connect of every new install.
    """
    return 0 if shown == FIRMWARE_TEXT_SPEED_MS else shown

#: A binding that names a macro slot may ask for it more than once. The count
#: rides in the spare `b` byte of that keymap entry, so the ceiling is what one
#: byte holds -- no EEPROM is spent on this and no schema bump is needed, the
#: same trick byte 11 plays for the typing speed.
#:
#: This is what makes a "real timing" recording usable at scale. A ten second
#: hold costs a press, four delays and a release, and the pad holds 308 records
#: in total; repeating it by recording it again 255 times does not fit, and a
#: single slot caps out at 42 passes. Looping it costs six records.
MIN_MACRO_REPEAT = 1
#: Must match MK_MACRO_MAX_LOOPS in firmware/src/Config.h.
MAX_MACRO_REPEAT = 255


def macro_repeat_stored(shown: int) -> int:
    """The editor's number as the pad stores it: once goes back as 0, not 1.

    A pad that has never been told a loop count has zero in that byte, and the
    app compares profiles as raw bytes -- writing 1 for the same behaviour would
    report a difference that isn't one, which is a "Profile differs" prompt on
    the first connect of every install. Exactly the trap `text_speed_stored`
    sidesteps, for exactly the same reason.
    """
    return 0 if shown <= MIN_MACRO_REPEAT else min(shown, MAX_MACRO_REPEAT)


def macro_repeat_shown(stored: int) -> int:
    """The stored byte as the editor spells it: 0 is one pass."""
    return stored or MIN_MACRO_REPEAT


#: The pause a repeated shortcut gets between presses.
#:
#: A shortcut is not a macro, and the four bytes it stores are all spoken for --
#: type, modifiers, keycode, flags -- so there is nowhere to put a count. Asking
#: for one wraps it into a one-shortcut macro instead, which costs a slot and
#: two records and brings the whole replay path with it: stopping the loop with
#: any pad key, the runaway budget, the busy pixel. None of that would exist on
#: a count squeezed into the flags byte.
#:
#: The pause is what the wrapping is for. `runMacro` does not rest between
#: passes, so ten presses would leave the pad inside a couple of milliseconds --
#: and a desktop opening a terminal per press does not see ten of them. 50 ms is
#: slow enough for a window manager to keep up and fast enough to still read as
#: one action.
SHORTCUT_REPEAT_GAP_MS = 50


def repeated_shortcut(macro: list[Action]) -> Action | None:
    """The shortcut a wrapped slot holds, or None when it is a real recording.

    Recognised by shape, because nothing else distinguishes the two: a wrapped
    shortcut is exactly one key action and the gap put there to pace it. The
    editor and the key grid use this so a repeated shortcut still reads as the
    shortcut it is rather than as "recording, 1 key", which is true and useless.

    The gap has to match exactly. A hand recording that happens to be one key
    and one pause would otherwise be relabelled, and a recorded pause landing on
    50 ms to the millisecond is not something a hand does.
    """
    if (
        len(macro) == 2
        and macro[0].kind == "key"
        and macro[1].kind == "delay"
        and macro[1].delay_ms == SHORTCUT_REPEAT_GAP_MS
    ):
        return macro[0]
    return None


def wrap_shortcut(action: Action) -> list[Action]:
    """The macro that replays `action` once, ready to be repeated."""
    return [replace(action, repeat=MIN_MACRO_REPEAT),
            Action(kind="delay", delay_ms=SHORTCUT_REPEAT_GAP_MS)]


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
    # 9 was ACT_HOST (desktop-run tokens). Retired: the pad is HID-only and the
    # PC app is config-only. The number stays reserved like 6 and 7.
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

#: 0x01 was KEYF_REPEAT, auto-repeat while a key is held. Nothing could reach
#: it once hold stopped being bindable -- tap and double both fire on release,
#: so the pad disarmed the repeat on the next scan without ever running it. The
#: bit stays reserved because it is a wire format; the field is gone.
KEYF_STICKY = 0x02

#: `Action.mode` for kind="mouse_button" and kind="key", stored differently on
#: the wire for each. Mouse puts it in the record's `b` byte. Keys use distinct
#: action type ids (ACT_KEY / ACT_KEY_PRESS / ACT_KEY_RELEASE) because a macro
#: record is only three bytes and the keycode already owns `b`.
#:
#: A press that is never released is what a hold (or a drag) is made of, so the
#: button/key is not implicitly let go the way a click does it.
MOUSE_MODES: dict[str, int] = {"click": 0, "press": 1, "release": 2}
ID_TO_MOUSE_MODE = {value: key for key, value in MOUSE_MODES.items()}
#: Wire type ids for kind="key" by mode. Click keeps ACT_KEY (= 1) so existing
#: profiles stay bit-identical; press/release are new ids past ACT_MOUSE_HOME.
KEY_MODE_TYPE_IDS: dict[str, int] = {"click": 1, "press": 14, "release": 15}
TYPE_ID_TO_KEY_MODE = {value: key for key, value in KEY_MODE_TYPE_IDS.items()}


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
    mode: str = "click"    # click, press or release -- press is half a hold/drag
    text: str = ""         # kind="text", macro records only
    dx: int = 0            # kind="mouse_move"
    dy: int = 0
    delta: int = 0         # kind="mouse_wheel"
    slot: int = 0          # kind="sequence"
    #: kind="sequence": how many times to replay the slot. 1 is once. The pad
    #: stops a loop of two or more when any of its keys is pressed; a single
    #: pass keeps queuing presses the way it always has.
    repeat: int = MIN_MACRO_REPEAT
    scene: int = 0         # kind="led_scene", reserved: the pad has one scene
    delay_ms: int = 0      # kind="delay", macro steps only
    #: A one-shot modifier: arms the modifiers for the *next* key rather than
    #: sending anything now. The firmware honours it; the editor does not offer
    #: it yet, so nothing currently sets it.
    sticky: bool = False

    def __post_init__(self) -> None:
        if self.kind not in ACTION_TYPE_IDS:
            raise ProfileError(f"unknown action kind: {self.kind!r}")
        if self.kind in ("mouse_button", "key") and self.mode not in MOUSE_MODES:
            raise ProfileError(f"unknown mode: {self.mode!r}")
        if self.kind == "sequence" and not (
            MIN_MACRO_REPEAT <= self.repeat <= MAX_MACRO_REPEAT
        ):
            raise ProfileError(
                f"a macro repeats between {MIN_MACRO_REPEAT} and "
                f"{MAX_MACRO_REPEAT} times, got {self.repeat}"
            )
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
            type_id = KEY_MODE_TYPE_IDS[self.mode]
            return type_id, modifiers, code, KEYF_STICKY if self.sticky else 0
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
            return (
                type_id,
                _clamp(self.slot, 0, MACRO_SLOTS - 1),
                macro_repeat_stored(self.repeat),
                0,  # reserved; widening the count into it is not worth the byte
            )
        if self.kind == "led_scene":
            return type_id, _clamp(self.scene, 0, 255), 0, 0
        if self.kind == "delay":
            return type_id, _clamp(round(self.delay_ms / 10), 0, 255), 0, 0
        return 0, 0, 0, 0

    @classmethod
    def decode(cls, type_id: int, a: int, b: int, c: int) -> Action:
        if type_id in TYPE_ID_TO_KEY_MODE:
            return cls(
                kind="key",
                hotkey=keycodes.format_hotkey(a, b),
                sticky=bool(c & KEYF_STICKY),
                mode=TYPE_ID_TO_KEY_MODE[type_id],
            )
        kind = ID_TO_KIND.get(type_id, "none")
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
            return cls(kind="sequence", slot=a, repeat=macro_repeat_shown(b))
        if kind == "led_scene":
            return cls(kind="led_scene", scene=a)
        if kind == "mouse_home":
            return cls(kind="mouse_home")
        if kind == "delay":
            return cls(kind="delay", delay_ms=a * 10)
        # Retired ids (layers 6/7, former host 9) decode as empty.
        return cls()

    def describe(self) -> str:
        """Human-readable summary, shown before a recording is saved."""
        if self.kind == "key":
            suffix = " then the next key" if self.sticky else ""
            if self.mode == "press":
                return f"hold {self.hotkey}{suffix}"
            if self.mode == "release":
                return f"let go of {self.hotkey}{suffix}"
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
            if self.repeat > MIN_MACRO_REPEAT:
                return f"device macro #{self.slot} x{self.repeat}"
            return f"device macro #{self.slot}"
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
        # Former desktop-run tokens have no pad equivalent; clear the slot.
        if data.get("kind") == "host":
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        # Drop fields retired with host actions (token) so old JSON still loads.
        known.pop("token", None)
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


#: A movement slice larger than this is replayed at the firmware's pacing cap
#: rather than at one report per count -- which is the point where the
#: desktop's acceleration curve stops cancelling out and starts deciding how
#: far the pointer actually goes.
#:
#: It mirrors the budget the firmware computes in `KeyEngine::emitMove`:
#: MK_MACRO_MOVE_SLICE_MS * MK_MACRO_MOVE_PACE_DEN / (poll * MK_MACRO_MOVE_PACE_NUM),
#: which is 50 * 2 / (1 * 3) on both boards today. Duplicated rather than
#: derived because this is only used to decide whether to *say* something, and
#: a number that drifts by a count or two changes nothing about that.
ACCEL_SENSITIVE_SLICE_COUNTS = 33


def accel_sensitive_macro_slots(macros: list[list[Action]]) -> list[int]:
    """Slots whose mouse movement is fast enough for acceleration to move it.

    Consecutive move records are one slice of motion -- the host splits a slice
    only because a count past 127 does not fit in a signed byte -- so they are
    summed before being measured, exactly as `KeyEngine::runMoves` does.

    A slow drag is replayed a count at a time, at the speed the hand made it,
    and any acceleration curve applies to the replay as it applied to the hand.
    A fast one cannot be: there are only so many USB frames in the pause, so
    the counts arrive in larger steps than the mouse sent them, and the curve
    no longer cancels. Those are the recordings worth warning about.
    """
    sensitive: list[int] = []
    for slot, macro in enumerate(macros):
        run_x = run_y = 0
        for action in list(macro) + [Action()]:
            if action.kind == "mouse_move":
                run_x += abs(action.dx)
                run_y += abs(action.dy)
                continue
            if max(run_x, run_y) > ACCEL_SENSITIVE_SLICE_COUNTS:
                sensitive.append(slot)
                break
            run_x = run_y = 0
    return sensitive


def macro_records(macro: list[Action]) -> int:
    """How many 3-byte records a compiled macro occupies on the device.

    Not ``len(macro)``: a text action spans a header plus its packed
    characters, and it is records, not actions, that the region runs out of.
    """
    return sum(action.record_count() for action in macro)


def macro_storage_usage(
    macros: list[list[Action]],
    *,
    capacity: int = MACRO_RECORD_CAPACITY,
) -> tuple[int, int, int, int]:
    """Pad macro-region fill: ``(used, capacity, used_pct, free_pct)``.

    Percentages are of the shared EEPROM pool (every slot together), not of one
    slot's 255-record ceiling. A near-empty pool rounds to 0% used; a full one
    is 100% used / 0% free.
    """
    used = sum(macro_records(macro) for macro in macros if macro)
    used = min(used, capacity)
    if capacity <= 0:
        return 0, 0, 0, 0
    used_pct = int(round(100.0 * used / capacity))
    used_pct = max(0, min(100, used_pct))
    return used, capacity, used_pct, 100 - used_pct


@dataclass
class Profile:
    schema_version: int = SCHEMA_VERSION
    name: str = "default"
    brightness: int = 64
    #: What the pixel rests at when nothing is happening, as RRGGBB.
    resting_color: str = DEFAULT_RESTING_COLOR
    #: Milliseconds between characters when the pad replays a typed run.
    text_speed_ms: int = DEFAULT_TEXT_SPEED_MS
    keys: list[KeySlot] = field(default_factory=list)
    # No chords. They were eight EEPROM slots the editor never offered a way to
    # fill and the defaults left empty, so the region only ever held zeroes --
    # forty bytes that macro records now use instead.
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

        Former ``host`` tokens are cleared too: the pad is HID-only now.
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

    # ------------------------------------------------------------- storage --

    def referenced_macro_slots(self) -> set[int]:
        """Macro slots some binding still points at."""
        slots: set[int] = set()
        for key in self.keys:
            for gesture in GESTURES:
                action = key.gesture(gesture)
                if action.kind == "sequence":
                    slots.add(action.slot)
        return slots

    def reclaim_storage(self) -> int:
        """Empties macro slots nothing points at any more.

        Recording into a key that already held one is the ordinary case -- it is
        how a macro gets corrected -- and every time it happened the old slot was
        left full. Sixteen corrections to a single key filled all sixteen slots
        with unreachable steps.

        Returns how many slots were freed.
        """
        slots = self.referenced_macro_slots()

        freed_slots = 0
        for index, macro in enumerate(self.device_macros):
            if macro and index not in slots:
                self.device_macros[index] = []
                freed_slots += 1

        return freed_slots

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "brightness": self.brightness,
            "resting_color": self.resting_color,
            "text_speed_ms": self.text_speed_ms,
            "keys": [key.to_dict() for key in self.keys],
            "device_macros": [
                [step.to_dict() for step in macro] for macro in self.device_macros
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        # host_actions are ignored: the pad no longer runs desktop macros.
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            name=str(data.get("name", "default")),
            brightness=_clamp(int(data.get("brightness", 64)), 0, 255),
            resting_color=str(data.get("resting_color", DEFAULT_RESTING_COLOR)),
            text_speed_ms=_clamp(
                int(data.get("text_speed_ms", DEFAULT_TEXT_SPEED_MS)), 0, MAX_TEXT_SPEED_MS
            ),
            keys=_keys_from_dict(data),
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

    return profile


def is_factory_default(profile: Profile) -> bool:
    """True when nothing has been bound or recorded onto this profile.

    Only the bindings and the recorded macros are compared -- those are the work
    someone would lose. Brightness, resting colour and typing speed are settings
    rather than content, and a pad whose brightness was nudged is still empty.

    Used to tell two situations apart that look identical to a byte comparison:
    a keypad that holds a different profile, and a keypad that has forgotten the
    one it had. The second must not be quietly copied over a computer that still
    has the macros.
    """
    if any(profile.device_macros):
        return False
    reference = default_profile()
    return all(
        profile.action(key, gesture) == reference.action(key, gesture)
        for key in range(KEY_COUNT)
        for gesture in EDITABLE_GESTURES
    )
