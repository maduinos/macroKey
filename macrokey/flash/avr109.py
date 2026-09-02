"""Writing firmware to a Caterina (AVR109) bootloader over the serial port.

The ATmega32u4's bootloader speaks Atmel's AVR109 "butterfly" protocol: a
handful of single-letter commands on a CDC port. avrdude implements it too, but
bundling avrdude means shipping a native binary and its configuration file for
every platform -- for a protocol that is this small.

Two things about it are easy to get wrong and impossible to notice afterwards:

* **Addresses are word addresses.** Flash is addressed in 16-bit words, so a
  byte offset is halved before it is sent. Getting this wrong writes a valid
  image to the wrong half of flash.
* **The bootloader is a stopwatch.** Caterina waits about 8 seconds and then
  jumps to the sketch, taking its port with it. Every read here has a timeout
  for that reason, and a silent read is reported rather than waited on.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path

from .errors import FlashError

try:  # pyserial is optional so the rest of the app runs without hardware
    import serial
except ModuleNotFoundError:  # pragma: no cover - exercised only without pyserial
    serial = None

#: Caterina identifies itself with this, and it is the check that the port
#: opened is a bootloader and not the sketch (or an unrelated CDC device).
EXPECTED_IDS = (b"CATERIN", b"LUFACDC")
#: Flash page on the 32u4. Writes are refused off a page boundary by the
#: bootloader, so blocks are cut to this.
PAGE_SIZE = 128
READ_TIMEOUT = 2.0


class HexError(FlashError):
    """The .hex file is not one this can write."""


def parse_hex(text: str) -> bytes:
    """Intel HEX to a flat image, padded with 0xFF between records.

    0xFF is erased flash, so gaps are left as they already are rather than
    written as zeroes -- writing zeroes would be a change, and one that cannot
    be undone without a full erase.
    """
    image = bytearray()
    base = 0
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise HexError(f"line {number}: no record mark")
        try:
            raw = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise HexError(f"line {number}: {exc}") from exc
        if len(raw) < 5 or raw[0] != len(raw) - 5:
            raise HexError(f"line {number}: truncated record")
        if (sum(raw) & 0xFF) != 0:
            raise HexError(f"line {number}: checksum mismatch")
        count, offset, kind, payload = raw[0], (raw[1] << 8) | raw[2], raw[3], raw[4:-1]
        if kind == 0x00:
            at = base + offset
            if at + count > len(image):
                image.extend(b"\xff" * (at + count - len(image)))
            image[at : at + count] = payload
        elif kind == 0x01:
            break
        elif kind == 0x02:
            base = ((payload[0] << 8) | payload[1]) * 16
        elif kind == 0x04:
            base = ((payload[0] << 8) | payload[1]) << 16
        elif kind in (0x03, 0x05):
            continue  # start address; nothing to place
        else:
            raise HexError(f"line {number}: unsupported record type {kind:#04x}")
    if not image:
        raise HexError("the hex file contains no data")
    # Whole pages: the bootloader writes a page at a time and a short final
    # block would leave the tail of that page holding whatever was there.
    if len(image) % PAGE_SIZE:
        image.extend(b"\xff" * (PAGE_SIZE - len(image) % PAGE_SIZE))
    return bytes(image)


def _pages(image: bytes) -> Iterator[tuple[int, bytes]]:
    for offset in range(0, len(image), PAGE_SIZE):
        yield offset, image[offset : offset + PAGE_SIZE]


class Programmer:
    """One session with a bootloader on one port."""

    def __init__(self, port: str, *, status: Callable[[str], None] = lambda _m: None) -> None:
        if serial is None:
            raise FlashError("pyserial is not installed: pip install pyserial")
        self.status = status
        try:
            # Caterina is a CDC device: the baud rate is ignored, but pyserial
            # insists on one. The timeout is what keeps a bootloader that has
            # already timed out from hanging this.
            self._port = serial.Serial(
                port, 57600, timeout=READ_TIMEOUT, write_timeout=READ_TIMEOUT
            )
        except serial.SerialException as exc:
            raise FlashError(f"could not open the bootloader on {port}: {exc}") from exc

    def close(self) -> None:
        try:
            self._port.close()
        except Exception:  # noqa: BLE001 - closing must never mask a real failure
            pass

    def __enter__(self) -> Programmer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ----------------------------------------------------------- primitives --

    def _command(self, payload: bytes, expect: int) -> bytes:
        self._port.write(payload)
        self._port.flush()
        reply = self._port.read(expect)
        if len(reply) != expect:
            raise FlashError(
                f"the bootloader stopped answering after {payload[:1]!r} "
                f"(wanted {expect} bytes, got {len(reply)}). It may have timed out "
                "and started the sketch; put the board back in its bootloader."
            )
        return reply

    def _expect_cr(self, payload: bytes) -> None:
        reply = self._command(payload, 1)
        if reply != b"\r":
            raise FlashError(f"the bootloader refused {payload[:1]!r} (answered {reply!r})")

    # -------------------------------------------------------------- session --

    def identify(self) -> str:
        name = self._command(b"S", 7)
        if name not in EXPECTED_IDS:
            raise FlashError(
                f"this port answers {name!r}, which is not a Caterina bootloader"
            )
        return name.decode("ascii", "replace")

    def set_address(self, byte_address: int) -> None:
        """AVR109 addresses flash in words, not bytes."""
        word = byte_address >> 1
        if word > 0xFFFF:
            raise FlashError("address past what a 16-bit word address can reach")
        self._expect_cr(bytes((ord("A"), (word >> 8) & 0xFF, word & 0xFF)))

    def write_page(self, data: bytes) -> None:
        header = bytes((ord("B"), (len(data) >> 8) & 0xFF, len(data) & 0xFF, ord("F")))
        self._expect_cr(header + data)

    def read_page(self, length: int) -> bytes:
        header = bytes((ord("g"), (length >> 8) & 0xFF, length & 0xFF, ord("F")))
        self._port.write(header)
        self._port.flush()
        data = self._port.read(length)
        if len(data) != length:
            raise FlashError("the bootloader returned a short read while verifying")
        return data

    def leave(self) -> None:
        """Leaves programming mode and starts the sketch."""
        self._expect_cr(b"L")
        # `E` makes the bootloader exit. It answers and then disappears, so a
        # missing reply here is the board doing exactly what was asked.
        try:
            self._port.write(b"E")
            self._port.flush()
            self._port.read(1)
        except Exception:  # noqa: BLE001 - the port going away is the success case
            pass


def write_image(
    image_path: Path,
    port: str,
    *,
    verify: bool = True,
    status: Callable[[str], None] = lambda _m: None,
) -> None:
    """Writes a .hex to the bootloader on `port`, and reads it back."""
    image = parse_hex(image_path.read_text("ascii", "replace"))
    status(f"Writing {len(image)} bytes to {port}")

    with Programmer(port, status=status) as programmer:
        programmer.identify()
        programmer.set_address(0)
        for offset, page in _pages(image):
            programmer.write_page(page)
            if offset and offset % (PAGE_SIZE * 32) == 0:
                status(f"Written {offset}/{len(image)} bytes")

        if verify:
            status("Verifying")
            programmer.set_address(0)
            for offset, page in _pages(image):
                back = programmer.read_page(len(page))
                if back != page:
                    raise FlashError(
                        f"verify failed at byte {offset}: the board did not keep "
                        "what was written. The image on it is incomplete -- flash "
                        "it again before unplugging."
                    )

        programmer.leave()
    status("Firmware written")
    # The sketch re-enumerates as a different USB device, which takes a moment.
    time.sleep(0.5)
