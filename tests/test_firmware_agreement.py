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

#: Sources the harness needs. LedEffects comes along because LedController uses it.
SOURCES = ("Profile.cpp", "Util.cpp", "KeyEngine.cpp", "ButtonInput.cpp",
           "LedController.cpp", "LedEffects.cpp")


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
    }


# ------------------------------------------------------------------ profile --


def sample_profile() -> model.Profile:
    profile = model.default_profile()
    profile.device_macros = [
        reduce_to_device_macro([
            {"type": "text", "params": {"text": "sudo apt update && sudo apt upgrade -y"}},
            {"type": "delay", "params": {"ms": 300}},
            {"type": "hotkey", "params": {"hotkey": "enter"}},
        ]),
        reduce_to_device_macro([
            {"type": "mouse_button", "params": {"button": "left", "mode": "press"}},
            {"type": "mouse_move", "params": {"dx": 300, "dy": -200}},
            {"type": "mouse_button", "params": {"button": "left", "mode": "release"}},
        ]),
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
