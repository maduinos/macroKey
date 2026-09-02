"""Finding the firmware image to write.

Images are built by CI, one per registered board, and travel with the app --
inside the PyInstaller bundle for a release, or in `firmware/prebuilt/` for a
source checkout. Nothing here downloads: a flasher that needs the network is
useless in the case it exists for, which is a pad that will not talk and a
person who wants it working now.
"""

from __future__ import annotations

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
