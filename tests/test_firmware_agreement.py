"""The firmware and the host must agree about the same 1024 bytes.

`firmware/src/Profile.h` and `macrokey/config/binary.py` each carry their own
copy of the EEPROM layout. Nothing checks them against each other at build time,
and a disagreement does not fail anywhere -- it produces a macro that replays
garbage, on a device with one pixel and no screen.

So the firmware is compiled here for the PC, against the stub headers in
`firmware/test/stubs`, and fed bytes the host encoder produced. What the
firmware's own reader makes of them is compared with what went in.

Skipped, not failed, when there is no C++ compiler: this is a cross-language
check, and a machine without one can still run everything else.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from macrokey.config import binary, model
from macrokey.recorder.normalize import reduce_to_device_macro

ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware"
HARNESS = FIRMWARE / "test" / "harness.cpp"

COMPILER = shutil.which("g++") or shutil.which("clang++")
pytestmark = pytest.mark.skipif(
    COMPILER is None, reason="no C++ compiler, so the firmware cannot be run here"
)

#: Sources the harness needs. LedEffects comes along because LedController uses
#: it, and SerialProtocol because a command's effect on the device -- not merely
#: its reply -- is the thing worth testing.
SOURCES = ("Profile.cpp", "Util.cpp", "KeyEngine.cpp", "ButtonInput.cpp",
           "LedController.cpp", "LedEffects.cpp", "SerialProtocol.cpp")


@pytest.fixture(scope="session")
def harness(tmp_path_factory) -> Path:
    """Builds the firmware for this machine, once."""
    binary_path = tmp_path_factory.mktemp("firmware") / "harness"
    command = [
        COMPILER, "-std=c++11", "-w", "-o", str(binary_path),
        "-I", str(FIRMWARE / "test" / "stubs"),
        "-I", str(FIRMWARE / "src"),
        str(HARNESS),
        *[str(FIRMWARE / "src" / name) for name in SOURCES],
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"the firmware did not build for this machine:\n{result.stderr}")
    return binary_path


def run(harness: Path, *args: str, blob: bytes | None = None) -> list[str]:
    result = subprocess.run(
        [str(harness), *args], input=blob, capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("ascii", "replace").splitlines()


def fields(lines: list[str]) -> dict[str, int]:
    return {line.split()[0]: int(line.split()[1]) for line in lines if line}


def serial(harness: Path, blob: bytes, *commands: str) -> tuple[dict[str, int], list[str]]:
    """Runs `commands` against the real serial layer on a pad holding `blob`.

    Returns the device state either side of them, and the lines the pad sent
    back. State first because the reply is rarely the interesting part: the
    failures this catches are commands that answered `OK` and left the pad
    holding something stale.
    """
    lines = run(harness, "serial", "".join(f"{line}\n" for line in commands), blob=blob)
    cut = lines.index("--- transcript")
    return fields(lines[:cut]), lines[cut + 1 :]


def profile_transfer(blob: bytes) -> list[str]:
    """The host half of a profile write, as lines."""
    import base64

    crc = binary.blob_crc(blob)
    lines = [f"PROF begin bytes={len(blob)} crc={crc:04X}"]
    for sequence, offset in enumerate(range(0, len(blob), binary.CHUNK_BYTES)):
        payload = base64.b64encode(blob[offset : offset + binary.CHUNK_BYTES]).decode()
        lines.append(f"PROF data seq={sequence} b64={payload}")
    lines.append("PROF commit")
    return lines


# ------------------------------------------------------------------- layout --


def test_the_two_layouts_are_the_same_layout(harness) -> None:
    """The check this whole file exists for."""
    seen = fields(run(harness, "layout"))
    assert seen == {
        "keymap_offset": binary.KEYMAP_OFFSET,
        "keymap_size": binary.KEYMAP_SIZE,
        "keymap_gestures": len(binary.KEYMAP_GESTURES),
        "palette_offset": binary.PALETTE_OFFSET,
        "macro_offset": binary.MACRO_OFFSET,
        "macro_index_size": binary.MACRO_INDEX_SIZE,
        "macro_record_size": binary.RECORD_SIZE,
        "macro_record_capacity": model.MACRO_RECORD_CAPACITY,
        "macro_max_records": model.MACRO_MAX_RECORDS,
        "macro_slots": model.MACRO_SLOTS,
        "profile_size": binary.PROFILE_SIZE,
        "schema": binary.SCHEMA,
        "text_delay_default": FIRMWARE_TEXT_DELAY_MS,
    }


#: What the firmware was built with, and what a profile of 0 falls back to.
FIRMWARE_TEXT_DELAY_MS = 5


def test_the_pad_honours_the_typing_speed_from_the_profile(harness) -> None:
    """Replay is faster than the recording was -- consecutive characters merge
    into one run with no timing kept -- so this is the knob for anything that
    has to catch up. It lives on the profile because the pad replays with the
    app closed, and zero means whatever the firmware was built with."""
    profile = sample_profile()

    profile.text_speed_ms = 0
    lines = run(harness, "profile", blob=binary.encode_profile(profile))
    assert f"text_delay {FIRMWARE_TEXT_DELAY_MS}" in lines

    profile.text_speed_ms = 40
    lines = run(harness, "profile", blob=binary.encode_profile(profile))
    assert "text_delay 40" in lines


def test_the_typing_speed_does_not_disturb_the_body(harness) -> None:
    """It lives in a header byte that was reserved and written as zero, which is
    why it needed no schema bump. The CRC covers the body, not the header."""
    slow, quick = sample_profile(), sample_profile()
    slow.text_speed_ms = 200
    assert binary.encode_profile(slow)[16:] == binary.encode_profile(quick)[16:]
    assert run(harness, "profile", blob=binary.encode_profile(slow))[0] == "valid 1"


# ------------------------------------------------------------------ profile --


def sample_profile() -> model.Profile:
    profile = model.default_profile()
    profile.device_macros = [
        reduce_to_device_macro([
            {"type": "text", "params": {"text": "sudo apt update && sudo apt upgrade -y"}},
            {"type": "delay", "params": {"ms": 300}},
            {"type": "hotkey", "params": {"hotkey": "enter"}},
        ]),
        reduce_to_device_macro(
            [
                {"type": "mouse_button", "params": {"button": "left", "mode": "press"}},
                {"type": "mouse_move", "params": {"dx": 300, "dy": -200}},
                {"type": "mouse_button", "params": {"button": "left", "mode": "release"}},
            ],
            anchor_pointer=True,
        ),
    ]
    profile.set_action(0, "tap", model.Action(kind="sequence", slot=0))
    profile.set_action(1, "double", model.Action(kind="sequence", slot=1))
    return profile


def test_the_firmware_accepts_a_profile_the_host_built(harness) -> None:
    """Magic, schema, topology and CRC, checked by the firmware's own code."""
    lines = run(harness, "profile", blob=binary.encode_profile(sample_profile()))
    assert "valid 1" in lines


def test_the_keymap_reads_back_as_it_was_written(harness) -> None:
    profile = sample_profile()
    lines = run(harness, "profile", blob=binary.encode_profile(profile))

    seen = {}
    for line in lines:
        if not line.startswith("key "):
            continue
        _, key, gesture, type_id, a, b = line.split()
        seen[(int(key), gesture)] = (int(type_id), int(a), int(b))

    for key in range(model.KEY_COUNT):
        for gesture in model.GESTURES:
            action = profile.action(key, gesture)
            expected = action.encode()[:3] if not action.is_empty else (0, 0, 0)
            assert seen[(key, gesture)] == expected, f"key {key + 1} {gesture}"


def test_hold_reads_as_empty_because_it_has_no_slot(harness) -> None:
    """It is how recording starts, so it is reported but never stored."""
    lines = run(harness, "profile", blob=binary.encode_profile(sample_profile()))
    holds = [line for line in lines if line.startswith("key ") and " hold " in line]
    assert len(holds) == model.KEY_COUNT
    assert all(line.endswith(" 0 0 0") for line in holds)


def test_the_macro_records_read_back_byte_for_byte(harness) -> None:
    """Where an index and a region that disagreed would show up."""
    profile = sample_profile()
    lines = run(harness, "profile", blob=binary.encode_profile(profile))

    seen = {}
    for line in lines:
        if not line.startswith("macro "):
            continue
        parts = line.split()
        seen[int(parts[1])] = [tuple(int(n) for n in item.split(",")) for item in parts[2:]]

    for slot, macro in enumerate(profile.device_macros):
        expected = [record for action in macro for record in action.records()]
        assert seen[slot] == expected, f"slot {slot}"


# ------------------------------------------------------------------ buttons --


def test_which_slot_a_hold_programs(harness) -> None:
    seen = {line.split()[0]: line.split()[1] for line in run(harness, "buttons")}
    assert seen == {
        "hold": "tap",
        "tap_then_hold": "double",       # double-click, then keep holding
        "tap_pause_hold": "tap",         # too slow to be a pair
        "short_hold": "none",            # 500 ms is not a request
        # Re-recording a double macro goes through the tap-deferral path, which
        # is only armed once the slot is full -- a different branch entirely.
        "tap_then_hold_when_double_is_bound": "double",
        "hold_just_after_boot": "tap",   # releasedAt starts at zero
    }


def test_the_buttons_are_still_scanned_while_a_macro_replays(harness) -> None:
    """A macro blocks loop(). If the scan stops with it, the second press of a
    double-record gesture is timestamped after the macro finishes -- seconds
    past the pair window -- so recording into the double slot was impossible on
    any key that already had a macro to replay.

    Slot 0, because it types and pauses and so lasts long enough for a press to
    debounce. Slot 1 is a drag with no pause in it: over in a few milliseconds,
    which is both too short to register a press and too short to matter.
    """
    lines = run(harness, "replay", "0", blob=binary.encode_profile(sample_profile()))
    assert "scanned-during-replay 1" in lines


# ------------------------------------------------------------------- replay --


def test_a_text_macro_types_its_characters(harness) -> None:
    blob = binary.encode_profile(sample_profile())
    typed = [
        chr(int(line.split()[1]))
        for line in run(harness, "replay", "0", blob=blob)
        if line.startswith("type ")
    ]
    assert "".join(typed) == "sudo apt update && sudo apt upgrade -y"


def test_a_drag_holds_the_button_across_the_movement(harness) -> None:
    lines = run(harness, "replay", "1", blob=binary.encode_profile(sample_profile()))
    mouse = [line for line in lines if line.startswith("mouse ")]

    # The pointer is driven into the corner first, so the drag starts from the
    # same pixel it was recorded from. That is a run of hard -127,-127 moves.
    homing = 0
    while mouse[homing] == "mouse move -127 -127":
        homing += 1
    assert homing >= 40, f"only {homing} homing steps"

    drag = mouse[homing:]
    assert drag[0] == "mouse press 1"
    moves = [line for line in drag if line.startswith("mouse move")]
    assert len(moves) == 3
    assert sum(int(line.split()[2]) for line in moves) == 300
    assert sum(int(line.split()[3]) for line in moves) == -200
    assert "mouse release 1" in drag


def test_authored_pauses_do_not_truncate_the_keys_after_them(harness) -> None:
    """MK_MACRO_MAX_RUN_MS used to be a plain wall clock. A recording with more
    than ten seconds of delays -- a real drag-then-Esc macro -- died before the
    Esc, and the pad looked like it had dropped the key. Authored waits extend
    the deadline; only runaway HID work spends the budget.
    """
    profile = model.default_profile()
    profile.device_macros = [
        [
            model.Action(kind="delay", delay_ms=12000),
            model.Action(kind="key", hotkey="esc"),
        ]
    ]
    lines = run(harness, "replay", "0", blob=binary.encode_profile(profile))
    # KEY_ESC is 0xB1 = 177 in Keyboard.h.
    assert "key press 177" in lines
    assert "key release 177" in lines


def test_a_text_run_that_overruns_its_slot_stops_the_macro(harness) -> None:
    """A text header says how many characters follow. Its end was computed in a
    byte, so a header late in a long slot wrapped: record 200 claiming 200
    characters worked out to 268, which truncated to 12 -- inside the slot, so
    the truncation check passed, and *behind* the header, so the macro replayed
    the same stretch for ever. The runaway deadline could not end it either,
    because every character pushes the deadline out by the pause it then waits.

    The pad cannot be made to hold such a slot by this app, and the profile CRC
    rules out getting there by corruption -- but the failure is a keypad that
    types nothing and answers nothing until it is unplugged, so the guard is
    worth having be a guard.
    """
    profile = model.default_profile()
    blob = bytearray(binary.encode_profile(profile))

    count = 210
    blob[binary.MACRO_OFFSET] = count
    base = binary.MACRO_OFFSET + binary.MACRO_INDEX_SIZE
    for index in range(count):
        at = base + index * binary.RECORD_SIZE
        blob[at : at + binary.RECORD_SIZE] = bytes(3)
    # Something visible inside the stretch the wrap would replay again.
    marker = model.Action(kind="key", hotkey="esc").encode()
    blob[base + 50 * 3 : base + 50 * 3 + 3] = bytes(marker[:3])
    # 1 + (200 + 2) // 3 = 68 records past record 200, which is 268 -> 12.
    blob[base + 200 * 3 : base + 200 * 3 + 3] = bytes((model.ACTION_TYPE_IDS["text"], 200, 0))
    crc = binary.crc16(bytes(blob[binary.HEADER_SIZE :]))
    blob[12], blob[13] = crc & 0xFF, crc >> 8

    lines = run(harness, "replay", "0", blob=bytes(blob))

    # KEY_ESC is 0xB1 = 177. Once: the run is refused as truncated and the
    # macro ends there. Looping, it is pressed until the deadline gives out.
    assert lines.count("key press 177") == 1


@pytest.mark.parametrize("slot", [0, 1])
def test_replay_never_leaves_a_key_or_button_held(harness, slot: int) -> None:
    """A press the host never sees released is a key it believes is still down,
    and that is indistinguishable from the keyboard having died. Every macro
    has to end with everything back up, however it ended."""
    lines = run(harness, "replay", str(slot), blob=binary.encode_profile(sample_profile()))

    held_keys: set[int] = set()
    held_buttons: set[int] = set()
    for line in lines:
        parts = line.split()
        if line.startswith("key press"):
            held_keys.add(int(parts[2]))
        elif line.startswith("key release-all"):
            held_keys.clear()
        elif line.startswith("key release"):
            held_keys.discard(int(parts[2]))
        elif line.startswith("mouse press"):
            held_buttons.add(int(parts[2]))
        elif line.startswith("mouse release"):
            held_buttons.discard(int(parts[2]))

    assert held_keys == set(), f"keys still held: {sorted(held_keys)}"
    assert held_buttons == set(), f"buttons still held: {sorted(held_buttons)}"


# ------------------------------------------------------------------ serial --


def stocked_profile() -> model.Profile:
    """A pad that is *not* at its defaults, so a reset has something to undo."""
    profile = model.default_profile()
    profile.brightness = 200
    profile.set_action(0, "double", model.Action(kind="key", hotkey="ctrl+z"))
    return profile


def test_reset_refreshes_what_the_rest_of_the_firmware_had_cached(harness) -> None:
    """`RESET defaults=1` rewrites the keymap under two things that had read it.

    The engine caches which keys have a double binding -- only those pay the
    double-tap delay -- and the pixel is driven from the LED controller's own
    copy of the brightness. Neither was told, so after a factory reset from the
    editor's Reset button a key whose double binding had just been erased still
    deferred its tap and then answered a double-tap with the unbound colour,
    and the pixel kept a brightness the app no longer showed. Until it was
    unplugged, which is not something the button says to do.
    """
    blob = binary.encode_profile(stocked_profile())

    state, replies = serial(harness, blob, "RESET defaults=1")

    assert state["mask_before"] == 0b1 and state["bright_before"] == 200
    assert state["mask_after"] == 0, "the erased double binding still defers its tap"
    assert state["bright_after"] == 64, "the pixel kept the old brightness"
    assert replies == ["OK"]


def test_a_written_profile_lights_the_pixel_at_its_own_brightness(harness) -> None:
    """The same staleness on the other write path.

    The GUI hid this by sending `LED bright=` before writing the profile, but
    `macrokey push` does not, so a profile whose brightness had changed took
    effect on everything except the light.
    """
    dim = stocked_profile()
    dim.brightness = 12
    state, _ = serial(harness, binary.encode_profile(stocked_profile()),
                      *profile_transfer(binary.encode_profile(dim)))

    assert state["bright_before"] == 200
    assert state["bright_after"] == 12


def test_boot_is_refused_where_there_is_no_way_into_the_bootloader(harness) -> None:
    """The AVR arm jumps to Caterina and never returns; every other build has to
    say it cannot, because a BOOT that silently did nothing looks exactly like a
    pad that stopped answering -- at the moment someone is re-flashing it.
    """
    _, replies = serial(harness, binary.encode_profile(model.default_profile()), "BOOT")

    assert replies == ["ERR code=unsupported"]


def test_the_harness_is_built_from_the_real_sources() -> None:
    """A harness quietly compiling its own copy would prove nothing."""
    text = HARNESS.read_text()
    assert '#include "Profile.h"' in text and '#include "KeyEngine.h"' in text
    for name in SOURCES:
        assert (FIRMWARE / "src" / name).exists(), name


def test_the_stubs_do_not_shadow_firmware_headers() -> None:
    """The stubs stand in for the Arduino core, never for anything in src/."""
    stubs = {path.name for path in (FIRMWARE / "test" / "stubs").rglob("*.h")}
    sources = {path.name for path in (FIRMWARE / "src").glob("*.h")}
    assert stubs & sources == set(), f"stub shadows a real header: {stubs & sources}"


if __name__ == "__main__":  # pragma: no cover - convenience
    sys.exit(pytest.main([__file__, "-v"]))
