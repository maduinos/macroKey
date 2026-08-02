"""Profile <-> device blob.

Byte-for-byte mirror of the EEPROM layout in ``arduino/macrokey/src/Profile.h``.
If that header changes, this module changes with it and ``SCHEMA`` goes up.
"""

from __future__ import annotations

from .model import (
    CHORD_SLOTS,
    GESTURES,
    KEY_COUNT,
    LAYER_COUNT,
    LED_COUNT,
    MACRO_SLOTS,
    MACRO_STEP_CAPACITY,
    Action,
    Chord,
    KeySlot,
    Layer,
    Profile,
    ProfileError,
)

MAGIC = b"MKEY"
SCHEMA = 1

HEADER_SIZE = 16
KEYMAP_OFFSET = HEADER_SIZE
KEYMAP_SIZE = LAYER_COUNT * KEY_COUNT * len(GESTURES) * 4
CHORD_OFFSET = KEYMAP_OFFSET + KEYMAP_SIZE
CHORD_ENTRY_SIZE = 5
CHORD_SIZE = CHORD_SLOTS * CHORD_ENTRY_SIZE
PALETTE_OFFSET = CHORD_OFFSET + CHORD_SIZE
PALETTE_SIZE = LAYER_COUNT * LED_COUNT * 3
MACRO_OFFSET = PALETTE_OFFSET + PALETTE_SIZE
MACRO_INDEX_SIZE = MACRO_SLOTS * 2
MACRO_REGION_SIZE = 480
PROFILE_SIZE = MACRO_OFFSET + MACRO_REGION_SIZE

CHUNK_BYTES = 48  # 48 raw bytes -> 64 base64 chars, inside the 96 byte line cap


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE, the same polynomial and seed the firmware uses."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _keymap_address(layer: int, key: int, gesture: int) -> int:
    index = (layer * KEY_COUNT + key) * len(GESTURES) + gesture
    return KEYMAP_OFFSET + index * 4


def _parse_color(text: str) -> tuple[int, int, int]:
    value = text.strip().lstrip("#")
    if len(value) != 6:
        raise ProfileError(f"colour must be RRGGBB, got {text!r}")
    try:
        packed = int(value, 16)
    except ValueError as exc:
        raise ProfileError(f"colour is not hex: {text!r}") from exc
    return (packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF


def encode_profile(profile: Profile) -> bytes:
    blob = bytearray(PROFILE_SIZE)

    blob[0:4] = MAGIC
    blob[4] = SCHEMA
    blob[5] = LAYER_COUNT
    blob[6] = KEY_COUNT
    blob[7] = len(GESTURES)
    blob[8] = profile.brightness & 0xFF
    blob[9] = profile.base_layer & 0xFF
    blob[10] = 0

    for layer_index, layer in enumerate(profile.layers[:LAYER_COUNT]):
        for key_index, slot in enumerate(layer.keys[:KEY_COUNT]):
            for gesture_index, gesture in enumerate(GESTURES):
                address = _keymap_address(layer_index, key_index, gesture_index)
                blob[address : address + 4] = bytes(slot.gesture(gesture).encode())

            red, green, blue = _parse_color(slot.color)
            palette = PALETTE_OFFSET + (layer_index * LED_COUNT + key_index) * 3
            blob[palette : palette + 3] = bytes((red, green, blue))

    for chord_index, chord in enumerate(profile.chords[:CHORD_SLOTS]):
        address = CHORD_OFFSET + chord_index * CHORD_ENTRY_SIZE
        blob[address] = chord.mask
        blob[address + 1 : address + 5] = bytes(chord.action.encode())

    _encode_macros(blob, profile)

    crc = crc16(bytes(blob[HEADER_SIZE:]))
    blob[12] = crc & 0xFF
    blob[13] = (crc >> 8) & 0xFF
    return bytes(blob)


def _encode_macros(blob: bytearray, profile: Profile) -> None:
    steps_base = MACRO_OFFSET + MACRO_INDEX_SIZE
    cursor = 0
    for slot_index, macro in enumerate(profile.device_macros[:MACRO_SLOTS]):
        steps = macro[:255]
        if cursor + len(steps) > MACRO_STEP_CAPACITY:
            raise ProfileError(
                f"device macro storage exhausted at slot {slot_index}: "
                f"{MACRO_STEP_CAPACITY} steps available. "
                "Move the longer macros to host actions."
            )
        index = MACRO_OFFSET + slot_index * 2
        blob[index] = cursor
        blob[index + 1] = len(steps)
        for step_index, step in enumerate(steps):
            type_id, a, b, _ = step.encode()
            address = steps_base + (cursor + step_index) * 3
            blob[address : address + 3] = bytes((type_id, a, b))
        cursor += len(steps)


def decode_profile(blob: bytes, *, name: str = "device") -> Profile:
    if len(blob) != PROFILE_SIZE:
        raise ProfileError(f"expected {PROFILE_SIZE} bytes, got {len(blob)}")
    if bytes(blob[0:4]) != MAGIC:
        raise ProfileError("bad magic: this is not a macroKey profile")
    if blob[4] != SCHEMA:
        raise ProfileError(f"unsupported device profile schema {blob[4]}")

    layers: list[Layer] = []
    for layer_index in range(LAYER_COUNT):
        slots: list[KeySlot] = []
        for key_index in range(KEY_COUNT):
            actions = {}
            for gesture_index, gesture in enumerate(GESTURES):
                address = _keymap_address(layer_index, key_index, gesture_index)
                actions[gesture] = Action.decode(*blob[address : address + 4])
            palette = PALETTE_OFFSET + (layer_index * LED_COUNT + key_index) * 3
            color = bytes(blob[palette : palette + 3]).hex()
            slots.append(KeySlot(color=color, **actions))
        layers.append(Layer(name=f"Layer {layer_index}", keys=slots))

    chords: list[Chord] = []
    for chord_index in range(CHORD_SLOTS):
        address = CHORD_OFFSET + chord_index * CHORD_ENTRY_SIZE
        mask = blob[address]
        action = Action.decode(*blob[address + 1 : address + 5])
        if mask == 0 or action.is_empty:
            continue
        keys = [bit for bit in range(KEY_COUNT) if mask & (1 << bit)]
        chords.append(Chord(keys=keys, action=action))

    macros: list[list[Action]] = []
    steps_base = MACRO_OFFSET + MACRO_INDEX_SIZE
    for slot_index in range(MACRO_SLOTS):
        index = MACRO_OFFSET + slot_index * 2
        offset, count = blob[index], blob[index + 1]
        steps = []
        for step_index in range(count):
            address = steps_base + (offset + step_index) * 3
            if address + 3 > PROFILE_SIZE:
                break
            steps.append(Action.decode(blob[address], blob[address + 1], blob[address + 2], 0))
        macros.append(steps)
    # Trailing empty slots carry no information; drop them so a decoded profile
    # compares equal to the one that produced it.
    while macros and not macros[-1]:
        macros.pop()

    return Profile(
        name=name,
        brightness=blob[8],
        base_layer=blob[9] if blob[9] < LAYER_COUNT else 0,
        layers=layers,
        chords=chords,
        device_macros=macros,
    )


def blob_crc(blob: bytes) -> int:
    """CRC over the body, which is what ``PROF begin crc=`` carries."""
    return crc16(blob[HEADER_SIZE:])
