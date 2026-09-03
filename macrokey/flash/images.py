"""Finding the firmware image to write.

Images are built by CI, one per registered board, and travel with the app --
inside the PyInstaller bundle for a release, or in `firmware/prebuilt/` for a
source checkout. Nothing here downloads: a flasher that needs the network is
useless in the case it exists for, which is a pad that will not talk and a
person who wants it working now.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ..boards import Board
from ..runtime import frozen
from .errors import NoImage

#: Where CI leaves the built images, and what `--add-data` puts in the bundle.
PREBUILT_DIRNAME = "prebuilt"


def search_paths() -> list[Path]:
    """Directories that may hold firmware images, most specific first."""
    paths: list[Path] = []
    if frozen():
        # PyInstaller unpacks --add-data here.
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            paths.append(Path(bundle) / "firmware" / PREBUILT_DIRNAME)
    root = Path(__file__).resolve().parent.parent.parent
    paths.append(root / "firmware" / PREBUILT_DIRNAME)
    return paths


def find_image(board: Board) -> Path:
    """The image for `board`, or `NoImage` naming where it was looked for."""
    looked: list[Path] = []
    for directory in search_paths():
        candidate = directory / board.firmware_name
        looked.append(candidate)
        if candidate.is_file():
            return candidate
    raise NoImage(
        f"no firmware image for {board.display_name}. Looked for "
        + ", ".join(str(path) for path in looked)
    )


def available() -> dict[str, Path]:
    """Board id -> image, for every board an image is on hand for."""
    from ..boards import BOARDS

    found: dict[str, Path] = {}
    for board in BOARDS:
        try:
            found[board.id] = find_image(board)
        except NoImage:
            continue
    return found


#: How the firmware announces itself in `HELLO`, and therefore what the string
#: looks like inside the image: `Serial.print(F(" fw=0.9.3 board=..."))`.
_VERSION_PATTERN = re.compile(rb"fw=(\d+\.\d+\.\d+)")


#: Ceiling on the flash image a HEX file may describe. Larger than any board
#: here by orders of magnitude, and the point is the other end: one two-byte
#: extended-address record says where the next data goes, so a corrupt file can
#: name address 0xFFFF0000 and ask this to fill four gigabytes to reach it.
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _intel_hex_payload(text: str) -> bytes:
    """The data records of an Intel HEX file, concatenated in address order."""
    blob = bytearray()
    base = 0
    for line in text.splitlines():
        if not line.startswith(":") or len(line) < 11:
            continue
        try:
            count = int(line[1:3], 16)
            offset = int(line[3:7], 16)
            kind = int(line[7:9], 16)
            body = bytes.fromhex(line[9 : 9 + 2 * count])
        except ValueError:
            continue
        if kind == 0x04 and len(body) == 2:  # extended linear address
            base = int.from_bytes(body, "big") << 16
        elif kind == 0x02 and len(body) == 2:  # extended segment address
            base = int.from_bytes(body, "big") << 4
        elif kind == 0x00:
            start = base + offset
            if start + count > _MAX_IMAGE_BYTES:
                continue
            if start + count > len(blob):
                blob.extend(b"\xff" * (start + count - len(blob)))
            blob[start : start + count] = body
    return bytes(blob)


def _uf2_payload(data: bytes) -> bytes:
    """The flash contents carried by a UF2 file, block payloads only."""
    blob = bytearray()
    for start in range(0, len(data) - 511, 512):
        block = data[start : start + 512]
        if block[:8] != b"UF2\n\x57\x51\x5d\x9e":
            continue
        size = int.from_bytes(block[16:20], "little")
        if size <= 476:
            blob.extend(block[32 : 32 + size])
    return bytes(blob)


def image_version(image: Path) -> str | None:
    """The firmware version an image will report, read out of the image itself.

    There is no manifest to trust and none is invented: the version is a string
    the firmware prints in `HELLO`, so it is in the flash contents verbatim and
    can simply be found there. That makes "is this image newer than what the pad
    is running?" a question about the two things themselves rather than about
    filenames or release notes, which are the parts that drift.

    None when the file is not an image this knows how to unpack, or carries no
    version string -- callers treat that as "cannot tell", never as "older".
    """
    try:
        raw = image.read_bytes()
    except OSError:
        return None
    if image.suffix.lower() == ".hex":
        payload = _intel_hex_payload(raw.decode("ascii", "replace"))
    elif image.suffix.lower() == ".uf2":
        payload = _uf2_payload(raw)
    else:
        payload = raw
    match = _VERSION_PATTERN.search(payload)
    return match.group(1).decode("ascii") if match else None
