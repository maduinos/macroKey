"""Flashing, which is the one part of the app that can brick the hardware.

Everything reachable without a board attached is exercised here: the Intel HEX
parser (a wrong image written to a bootloader is the worst outcome in the
project), volume discovery, image lookup, and the decisions the orchestrator
makes about what state a board is in. Writing to real hardware is not something
a test suite can do, so the seam is `Found` -- everything above it is checked
here, and the two `write_image` calls below it are thin on purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from macrokey.boards import PROMICRO_32U4, PROMICRO_RP2040
from macrokey.flash import attached, avr109, images, service, uf2
from macrokey.flash.errors import (
    BootloaderTimeout,
    FlashError,
    NeedsManualBootloader,
    NoImage,
)

# ------------------------------------------------------------------ intel hex --


def record(kind: int, offset: int, payload: bytes) -> str:
    raw = bytes([len(payload), (offset >> 8) & 0xFF, offset & 0xFF, kind]) + payload
    return ":" + (raw + bytes([(-sum(raw)) & 0xFF])).hex().upper()


def hex_file(*records: str) -> str:
    return "\n".join([*records, record(0x01, 0, b"")]) + "\n"


def test_records_land_at_their_addresses() -> None:
    image = avr109.parse_hex(
        hex_file(record(0x00, 0x0000, b"\x01\x02"), record(0x00, 0x0010, b"\x03\x04"))
    )
    assert image[0:2] == b"\x01\x02"
    assert image[0x10:0x12] == b"\x03\x04"


def test_gaps_are_erased_flash_and_not_zeroes() -> None:
    """0x00 is a value; 0xFF is "nothing was written here". Filling a gap with
    zeroes writes bytes the image never asked for, and flash cannot take them
    back without an erase."""
    image = avr109.parse_hex(hex_file(record(0x00, 0x0000, b"\x01"), record(0x00, 0x0008, b"\x02")))
    assert image[1:8] == b"\xff" * 7


def test_the_image_is_whole_pages() -> None:
    """The bootloader writes a page at a time. A short final block leaves the
    rest of that page holding whatever was there before."""
    image = avr109.parse_hex(hex_file(record(0x00, 0, b"\x01")))
    assert len(image) == avr109.PAGE_SIZE


def test_an_extended_address_record_moves_the_base() -> None:
    image = avr109.parse_hex(
        hex_file(record(0x04, 0, b"\x00\x01"), record(0x00, 0x0000, b"\xaa"))
    )
    assert image[0x10000] == 0xAA


@pytest.mark.parametrize(
    "text, why",
    [
        ("", "no records at all"),
        ("nonsense\n", "no record mark"),
        (":10000000FFFF\n", "truncated"),
        (":020000000102FF\n", "checksum"),
        (hex_file(record(0x06, 0, b"\x01")), "unsupported record type"),
    ],
)
def test_a_hex_file_that_is_not_right_is_refused(text: str, why: str) -> None:
    """Refusing is the whole job: a corrupt image written to a bootloader is a
    board that no longer enumerates, and there is no undo."""
    with pytest.raises(avr109.HexError):
        avr109.parse_hex(text)


def test_the_real_built_image_parses() -> None:
    built = Path(__file__).resolve().parent.parent / "firmware" / "prebuilt"
    image = built / PROMICRO_32U4.firmware_name
    if not image.is_file():
        pytest.skip("no firmware built here; run tools/build_firmware.sh")
    parsed = avr109.parse_hex(image.read_text("ascii", "replace"))
    assert len(parsed) % avr109.PAGE_SIZE == 0
    # 32u4 flash minus the 4 KB Caterina bootloader.
    assert 0 < len(parsed) <= 28672


# ---------------------------------------------------------------- addressing --


def test_flash_is_addressed_in_words_not_bytes(monkeypatch) -> None:
    """The mistake that writes a perfectly good image to the wrong half of
    flash, and reports success doing it."""
    written: list[bytes] = []

    class FakePort:
        def write(self, payload: bytes) -> None:
            written.append(payload)

        def flush(self) -> None:
            pass

        def read(self, count: int) -> bytes:
            return b"\r" * count

    programmer = avr109.Programmer.__new__(avr109.Programmer)
    programmer._port = FakePort()
    programmer.status = lambda _message: None

    programmer.set_address(0x0200)
    assert written == [bytes((ord("A"), 0x01, 0x00))]


# -------------------------------------------------------------------- images --


def test_an_image_is_found_where_ci_leaves_it(tmp_path, monkeypatch) -> None:
    directory = tmp_path / "prebuilt"
    directory.mkdir()
    (directory / PROMICRO_RP2040.firmware_name).write_bytes(b"UF2\n")
    monkeypatch.setattr(images, "search_paths", lambda: [directory])
    assert images.find_image(PROMICRO_RP2040).parent == directory


def test_a_missing_image_names_where_it_looked(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(images, "search_paths", lambda: [tmp_path])
    with pytest.raises(NoImage) as caught:
        images.find_image(PROMICRO_RP2040)
    assert str(tmp_path) in str(caught.value)


# ------------------------------------------------------------------- volumes --


def test_a_uf2_volume_is_found_by_label(tmp_path, monkeypatch) -> None:
    volume = tmp_path / "RPI-RP2"
    volume.mkdir()
    (volume / uf2.INFO_FILE).write_text("UF2 Bootloader\n")
    monkeypatch.setattr(uf2, "_mount_roots", lambda: [tmp_path])
    monkeypatch.setattr(uf2.sys, "platform", "linux")
    assert uf2.find_volume(PROMICRO_RP2040) == volume


def test_an_empty_mount_point_is_not_a_mounted_bootloader(tmp_path, monkeypatch) -> None:
    """The bug real hardware found.

    udisks creates /media/<user>/RPI-RP2 a moment before it mounts the board's
    filesystem on it. Matching the label alone declared the bootloader ready
    while that directory was still empty and owned by root, so the image was
    copied into an ordinary directory -- EACCES here, and on a machine where
    that directory happened to be writable, a silent success that flashed
    nothing at all.
    """
    volume = tmp_path / "RPI-RP2"
    volume.mkdir()  # the right name, nothing mounted on it
    monkeypatch.setattr(uf2, "_mount_roots", lambda: [tmp_path])
    monkeypatch.setattr(uf2.sys, "platform", "linux")

    assert uf2.find_volume(PROMICRO_RP2040) is None


def test_waiting_holds_out_for_a_writable_volume(tmp_path, monkeypatch) -> None:
    """Mounted read-only for a moment is a normal step, not a failure."""
    volume = tmp_path / "RPI-RP2"
    volume.mkdir()
    (volume / uf2.INFO_FILE).write_text("UF2 Bootloader\n")
    monkeypatch.setattr(uf2, "_mount_roots", lambda: [tmp_path])
    monkeypatch.setattr(uf2.sys, "platform", "linux")

    writable = iter([False, False, True])
    monkeypatch.setattr(uf2, "_writable", lambda _path: next(writable))
    assert uf2.wait_for_volume(PROMICRO_RP2040, timeout=5, poll=0.01) == volume


def test_waiting_gives_up_rather_than_hanging(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uf2, "_mount_roots", lambda: [tmp_path])
    monkeypatch.setattr(uf2.sys, "platform", "linux")
    with pytest.raises(BootloaderTimeout):
        uf2.wait_for_volume(PROMICRO_RP2040, timeout=0.2, poll=0.05)


def test_a_uf2_volume_is_found_by_its_info_file(tmp_path, monkeypatch) -> None:
    """Desktops rename mounts, and labels differ between board revisions. Every
    UF2 bootloader drops this file, so it is the more reliable half."""
    volume = tmp_path / "mounted-somewhere-else"
    volume.mkdir()
    (volume / uf2.INFO_FILE).write_text("UF2 Bootloader\n")
    monkeypatch.setattr(uf2, "_mount_roots", lambda: [tmp_path])
    monkeypatch.setattr(uf2.sys, "platform", "linux")
    assert uf2.find_volume(PROMICRO_RP2040) == volume


def test_an_unrelated_drive_is_not_mistaken_for_a_bootloader(tmp_path, monkeypatch) -> None:
    drive = tmp_path / "USB DISK"
    drive.mkdir()
    (drive / "holiday.jpg").write_bytes(b"")
    monkeypatch.setattr(uf2, "_mount_roots", lambda: [tmp_path])
    monkeypatch.setattr(uf2.sys, "platform", "linux")
    assert uf2.find_volume(PROMICRO_RP2040) is None


def test_a_board_flashed_over_serial_has_no_volume_to_find() -> None:
    assert uf2.find_volume(PROMICRO_32U4) is None


def test_writing_a_uf2_copies_it_onto_the_drive(tmp_path) -> None:
    image = tmp_path / PROMICRO_RP2040.firmware_name
    image.write_bytes(b"UF2\x0a" + b"\x00" * 508)
    volume = tmp_path / "RPI-RP2"
    volume.mkdir()
    uf2.write_image(image, volume)
    assert (volume / image.name).read_bytes() == image.read_bytes()


def test_writing_a_missing_image_fails_before_touching_the_drive(tmp_path) -> None:
    volume = tmp_path / "RPI-RP2"
    volume.mkdir()
    with pytest.raises(FlashError):
        uf2.write_image(tmp_path / "not-there.uf2", volume)
    assert list(volume.iterdir()) == []


# ------------------------------------------------------------------- detection --


class FakeCandidate:
    def __init__(self, device: str, board, bootloader: bool) -> None:
        self.device = device
        self.board = board
        self.in_bootloader = bootloader


def test_a_bootloader_sorts_before_a_running_board(monkeypatch) -> None:
    """A board in its bootloader is unambiguous and immediately actionable; a
    running one still has to be asked what it is holding."""
    monkeypatch.setattr(
        attached,
        "candidates",
        lambda: [
            FakeCandidate("/dev/running", PROMICRO_RP2040, False),
            FakeCandidate("/dev/boot", PROMICRO_32U4, True),
        ],
    )
    monkeypatch.setattr(attached.uf2, "find_volume", lambda _board: None)
    found = attached.detect()
    assert [item.port for item in found] == ["/dev/boot", "/dev/running"]
    assert found[0].ready_to_flash and not found[1].ready_to_flash


def test_a_uf2_bootloader_is_found_as_a_drive_not_a_port(tmp_path, monkeypatch) -> None:
    """It is mass storage, so it never appears in the serial port list -- which
    is exactly the board most in need of flashing."""
    volume = tmp_path / "RPI-RP2"
    volume.mkdir()
    monkeypatch.setattr(attached, "candidates", lambda: [])
    monkeypatch.setattr(
        attached.uf2, "find_volume", lambda board: volume if board.flash_method == "uf2" else None
    )
    found = attached.detect()
    assert len(found) == 1
    assert found[0].volume == volume and found[0].port is None
    assert found[0].ready_to_flash


def test_an_unknown_usb_device_is_not_offered_firmware(monkeypatch) -> None:
    monkeypatch.setattr(attached, "candidates", lambda: [FakeCandidate("/dev/x", None, False)])
    monkeypatch.setattr(attached.uf2, "find_volume", lambda _board: None)
    assert attached.detect() == []


# ---------------------------------------------------------------- orchestration --


def test_a_board_already_in_its_bootloader_is_not_asked_to_reboot(monkeypatch) -> None:
    found = attached.Found(board=PROMICRO_32U4, port="/dev/boot", bootloader=True)
    monkeypatch.setattr(
        service, "_touch_1200", lambda _port: pytest.fail("it was already there")
    )
    assert service.enter_bootloader(found, status=lambda _m: None) is found


def test_a_board_with_no_port_needs_a_person(monkeypatch) -> None:
    """The one thing no software can do: a board that has never run macroKey
    firmware has nothing listening for a request to reboot."""
    found = attached.Found(board=PROMICRO_RP2040, port=None, bootloader=False)
    with pytest.raises(NeedsManualBootloader) as caught:
        service.enter_bootloader(found, status=lambda _m: None)
    assert caught.value.hint == PROMICRO_RP2040.first_flash_hint
    assert "BOOTSEL" in caught.value.hint


def test_a_running_board_is_rebooted_and_then_waited_for(monkeypatch, tmp_path) -> None:
    volume = tmp_path / "RPI-RP2"
    volume.mkdir()
    touched: list[str] = []
    monkeypatch.setattr(service, "_touch_1200", touched.append)
    monkeypatch.setattr(service.uf2, "wait_for_volume", lambda *a, **k: volume)

    ready = service.enter_bootloader(
        attached.Found(board=PROMICRO_RP2040, port="/dev/ttyACM0"), status=lambda _m: None
    )
    assert touched == ["/dev/ttyACM0"]
    assert ready.volume == volume and ready.ready_to_flash


def test_each_board_is_written_by_its_own_method(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(service.uf2, "write_image", lambda *a, **k: calls.append("uf2"))
    monkeypatch.setattr(service.avr109, "write_image", lambda *a, **k: calls.append("avr109"))

    service.write(
        attached.Found(board=PROMICRO_RP2040, volume=tmp_path, bootloader=True),
        tmp_path / "image.uf2",
        status=lambda _m: None,
    )
    service.write(
        attached.Found(board=PROMICRO_32U4, port="/dev/boot", bootloader=True),
        tmp_path / "image.hex",
        status=lambda _m: None,
    )
    assert calls == ["uf2", "avr109"]
