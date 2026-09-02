"""What is plugged in, and what state it is in.

Named for the question rather than the verb: `attached.detect()` reads as what
it is, and a module called `detect` holding a function called `detect` cannot
both be reached through the package.

Three things can be true of a board on the end of the cable, and they need
different handling:

* it is running macroKey firmware -- it says so itself, over the protocol;
* it is a board macroKey knows, running something else or nothing at all --
  only its USB id says so, which is enough to offer to flash it;
* it is already sitting in its bootloader -- flash it, do not try to talk to it.

The third is why a plain "connect and ask" is not enough: a board waiting in
its bootloader answers no protocol at all, and that is exactly the board most
in need of being flashed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..boards import Board
from ..device.discovery import candidates
from . import uf2


@dataclass(frozen=True)
class Found:
    """A board on this machine, and how to reach it."""

    board: Board
    #: Serial port it is on, when it has one. A UF2 bootloader has none.
    port: str | None = None
    #: Mounted bootloader volume, for boards flashed by copying a file.
    volume: Path | None = None
    #: In its bootloader now, so it is ready to be written and cannot be talked to.
    bootloader: bool = False

    @property
    def ready_to_flash(self) -> bool:
        return self.bootloader and (self.port is not None or self.volume is not None)

    def __str__(self) -> str:
        where = self.volume or self.port or "?"
        state = "bootloader" if self.bootloader else "running"
        return f"{self.board.display_name} on {where} ({state})"


def detect() -> list[Found]:
    """Every registered board attached right now, bootloaders first.

    Bootloaders sort first because a board in one is unambiguous and immediately
    actionable, while a running board still has to be asked what firmware it
    holds.
    """
    found: list[Found] = []
    for candidate in candidates():
        board = candidate.board
        if board is None:
            continue
        found.append(
            Found(
                board=board,
                port=candidate.device,
                bootloader=candidate.in_bootloader,
            )
        )
    # UF2 bootloaders are drives, not ports, so they are invisible to the scan
    # above and have to be looked for separately.
    for board in _uf2_boards():
        volume = uf2.find_volume(board)
        if volume is not None:
            found.append(Found(board=board, volume=volume, bootloader=True))
    found.sort(key=lambda item: (not item.bootloader, item.board.id))
    return found


def _uf2_boards() -> list[Board]:
    from ..boards import BOARDS

    return [board for board in BOARDS if board.flash_method == "uf2"]


def find_board(board_id: str | None = None) -> Found | None:
    """The one board to act on, or None when nothing recognisable is attached."""
    for item in detect():
        if board_id is None or item.board.id == board_id:
            return item
    return None
