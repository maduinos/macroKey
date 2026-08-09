"""Profile <-> device blob.

Byte-for-byte mirror of the EEPROM layout in ``firmware/src/Profile.h``.
If that header changes, this module changes with it and ``SCHEMA`` goes up.
"""

from __future__ import annotations

from .model import (
    ACTION_TYPE_IDS,
    EDITABLE_GESTURES,
    KEY_COUNT,
    LED_COUNT,
    MACRO_MAX_RECORDS,
    MACRO_RECORD_CAPACITY,
    MACRO_SLOTS,
    MAX_TEXT_SPEED_MS,
    Action,
    KeySlot,
    Profile,
    ProfileError,
)

MAGIC = b"MKEY"
#: Bumped from 1 with the layout below. The blob is the same 1024 bytes either
#: way, so without this a pad holding the old arrangement would decode as
#: nonsense rather than be recognised as out of date.
SCHEMA = 2

HEADER_SIZE = 16
KEYMAP_OFFSET = HEADER_SIZE
#: Only the gestures that can be bound are stored. Hold is how recording starts,
#: so its slot held nothing but zeroes on every key -- 32 bytes of them.
KEYMAP_GESTURES = EDITABLE_GESTURES
KEYMAP_SIZE = KEY_COUNT * len(KEYMAP_GESTURES) * 4
#: No chord region. Eight slots at five bytes that nothing could fill: the
#: editor never offered them and the defaults left them empty.
PALETTE_OFFSET = KEYMAP_OFFSET + KEYMAP_SIZE
PALETTE_SIZE = LED_COUNT * 3
MACRO_OFFSET = PALETTE_OFFSET + PALETTE_SIZE
#: One byte per slot: how many records it uses. There is no stored offset --
#: slots are packed in order, so a slot's start is the sum of the counts before
#: it. That is 32 bytes back, and it retires a whole class of bug: an index and
#: a region that disagreed about where a macro began.
MACRO_INDEX_SIZE = MACRO_SLOTS
#: The ATmega32u4's whole EEPROM. Must match MK_EEPROM_SIZE.
EEPROM_SIZE = 1024
#: Everything the fixed regions do not use. Must match MK_MACRO_REGION_SIZE.
MACRO_REGION_SIZE = EEPROM_SIZE - MACRO_OFFSET
PROFILE_SIZE = MACRO_OFFSET + MACRO_REGION_SIZE
RECORD_SIZE = 3
#: What the region actually holds. `MACRO_RECORD_CAPACITY` in the model is this
#: number; the assert is what keeps the two from drifting apart.
_RECORD_CAPACITY = (MACRO_REGION_SIZE - MACRO_INDEX_SIZE) // RECORD_SIZE
assert _RECORD_CAPACITY == MACRO_RECORD_CAPACITY, (
    f"model says {MACRO_RECORD_CAPACITY} records, the layout holds {_RECORD_CAPACITY}"
)
assert PROFILE_SIZE == EEPROM_SIZE, "the profile is meant to be the whole EEPROM"

_TEXT_TYPE = ACTION_TYPE_IDS["text"]

CHUNK_BYTES = 48  # 48 raw bytes -> 64 base64 chars, inside the 96 byte line cap


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE, the same polynomial and seed the firmware uses."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _keymap_address(key: int, gesture: int) -> int:
    return KEYMAP_OFFSET + (key * len(KEYMAP_GESTURES) + gesture) * 4


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
    # Byte 5 was the layer count and byte 9 the base layer. Both are written as
    # the constants they became rather than reused: the firmware validates the
    # topology bytes on boot, so moving anything here would reject every pad.
    blob[5] = 1
    blob[6] = KEY_COUNT
    blob[7] = len(KEYMAP_GESTURES)
    blob[8] = profile.brightness & 0xFF
    blob[9] = 0
    blob[10] = 0
    # Byte 11 was reserved and written as zero, so putting the typing speed here
    # needs no schema bump: zero still means "whatever the firmware was built
    # with". The CRC covers the body from byte 16, not the header.
    blob[11] = max(0, min(MAX_TEXT_SPEED_MS, profile.text_speed_ms))

    for key_index, slot in enumerate(profile.keys[:KEY_COUNT]):
        for gesture_index, gesture in enumerate(KEYMAP_GESTURES):
            address = _keymap_address(key_index, gesture_index)
            blob[address : address + 4] = bytes(slot.gesture(gesture).encode())

    # One palette entry per pixel. The pad rests at this colour.
    red, green, blue = _parse_color(profile.resting_color)
    blob[PALETTE_OFFSET : PALETTE_OFFSET + 3] = bytes((red, green, blue))

    _encode_macros(blob, profile)

    crc = crc16(bytes(blob[HEADER_SIZE:]))
    blob[12] = crc & 0xFF
    blob[13] = (crc >> 8) & 0xFF
    return bytes(blob)


def _encode_macros(blob: bytearray, profile: Profile) -> None:
    """Packs every slot's records back to back, counts only in the index."""
    records_base = MACRO_OFFSET + MACRO_INDEX_SIZE
    cursor = 0
    for slot_index, macro in enumerate(profile.device_macros[:MACRO_SLOTS]):
        records: list[tuple[int, int, int]] = []
        for action in macro:
            records.extend(action.records())
        if len(records) > MACRO_MAX_RECORDS:
            raise ProfileError(
                f"macro slot {slot_index} needs {len(records)} records; "
                f"a slot holds at most {MACRO_MAX_RECORDS}."
            )
        if cursor + len(records) > MACRO_RECORD_CAPACITY:
            raise ProfileError(
                f"device macro storage exhausted at slot {slot_index}: "
                f"{MACRO_RECORD_CAPACITY} records available. "
                "Move the longer macros to host actions."
            )
        blob[MACRO_OFFSET + slot_index] = len(records)
        for record_index, record in enumerate(records):
            address = records_base + (cursor + record_index) * RECORD_SIZE
            blob[address : address + RECORD_SIZE] = bytes(record)
        cursor += len(records)


def decode_profile(blob: bytes, *, name: str = "device") -> Profile:
    if len(blob) != PROFILE_SIZE:
        raise ProfileError(f"expected {PROFILE_SIZE} bytes, got {len(blob)}")
    if bytes(blob[0:4]) != MAGIC:
        raise ProfileError("bad magic: this is not a macroKey profile")
    if blob[4] != SCHEMA:
        raise ProfileError(
            f"device profile is schema {blob[4]}, this app builds {SCHEMA}. "
            "Firmware and app are out of step -- re-flash the pad."
        )

    keys: list[KeySlot] = []
    for key_index in range(KEY_COUNT):
        actions = {}
        for gesture_index, gesture in enumerate(KEYMAP_GESTURES):
            address = _keymap_address(key_index, gesture_index)
            actions[gesture] = Action.decode(*blob[address : address + 4])
        keys.append(KeySlot(**actions))

    macros: list[list[Action]] = []
    records_base = MACRO_OFFSET + MACRO_INDEX_SIZE
    cursor = 0  # slots are packed in order, so this is each slot's start
    for slot_index in range(MACRO_SLOTS):
        count = blob[MACRO_OFFSET + slot_index]
        macros.append(_decode_macro(blob, records_base, cursor, count))
        cursor += count
    # Trailing empty slots carry no information; drop them so a decoded profile
    # compares equal to the one that produced it.
    while macros and not macros[-1]:
        macros.pop()

    return Profile(
        name=name,
        brightness=blob[8],
        resting_color=bytes(blob[PALETTE_OFFSET : PALETTE_OFFSET + 3]).hex(),
        text_speed_ms=blob[11],
        keys=keys,
        device_macros=macros,
    )


def _decode_macro(blob: bytes, base: int, start: int, count: int) -> list[Action]:
    """Reads one slot back, folding text payload records into their run.

    A text header says how many characters follow, packed three to a record.
    Those records are not actions and must not be decoded as any -- their bytes
    are ASCII, and read as action types they would be nonsense.
    """
    steps: list[Action] = []
    index = 0
    while index < count:
        address = base + (start + index) * RECORD_SIZE
        if address + RECORD_SIZE > PROFILE_SIZE:
            break
        type_id, a, b = blob[address], blob[address + 1], blob[address + 2]

        if type_id != _TEXT_TYPE:
            steps.append(Action.decode(type_id, a, b, 0))
            index += 1
            continue

        payload_records = (a + 2) // 3
        if index + 1 + payload_records > count:
            break  # truncated run: better to stop than invent characters
        raw = bytearray()
        for record in range(payload_records):
            at = base + (start + index + 1 + record) * RECORD_SIZE
            raw += blob[at : at + RECORD_SIZE]
        text = raw[:a].decode("ascii", errors="replace")
        if text:
            steps.append(Action(kind="text", text=text))
        index += 1 + payload_records
    return steps


def blob_crc(blob: bytes) -> int:
    """CRC over the body, which is what ``PROF begin crc=`` carries."""
    return crc16(blob[HEADER_SIZE:])
