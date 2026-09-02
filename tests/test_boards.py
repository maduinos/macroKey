"""The board registry has to stay internally consistent to be worth having.

`macrokey/boards.py` is the one place a board is described, and everything else
-- profile layouts, USB recognition, firmware images, the flashing method --
reads it rather than repeating it. That only holds while the table itself is
sane: two boards claiming one USB id, or one profile size, make identification
ambiguous in a way that shows up as the wrong storage ceiling rather than as an
error.

These are cheap, and they run against whatever is registered, so a third board
is checked the moment it is added.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from macrokey import boards
from macrokey.boards import BOARDS, Board
from macrokey.config import binary

ROOT = Path(__file__).resolve().parent.parent


def test_there_is_at_least_one_board() -> None:
    assert BOARDS, "the registry cannot be empty; the app has to assume something"


def test_ids_are_unique() -> None:
    ids = [board.id for board in BOARDS]
    assert len(ids) == len(set(ids)), f"duplicate board id: {ids}"


def test_profile_sizes_are_unique() -> None:
    """A blob's length identifies its board, and `board_by_profile_size` says so.

    Two boards of one size would make that lookup pick whichever was registered
    first. If it ever needs to happen, identification has to move to `HELLO
    board=` alone and this test is where the change starts.
    """
    sizes = [board.profile_size for board in BOARDS]
    assert len(sizes) == len(set(sizes)), f"two boards share a profile size: {sizes}"


def test_no_two_boards_claim_the_same_usb_id() -> None:
    """Otherwise a plugged-in board is recognised as the wrong one, and the app
    offers it the wrong firmware image -- which it would then flash."""
    seen: dict[tuple[int, int], str] = {}
    for board in BOARDS:
        for usb_id in board.usb_ids:
            clash = seen.get(usb_id)
            assert clash is None, (
                f"{usb_id[0]:04x}:{usb_id[1]:04x} is claimed by both {clash} and {board.id}"
            )
            seen[usb_id] = board.id


@pytest.mark.parametrize("board", BOARDS, ids=[board.id for board in BOARDS])
def test_a_slot_count_can_address_the_slot_ceiling(board: Board) -> None:
    """The index stores a slot's length in `count_bytes` bytes. A ceiling past
    what that can hold does not fail: the count wraps and the pad replays a
    fraction of the macro."""
    assert board.max_records_per_slot <= 2 ** (8 * board.count_bytes) - 1


@pytest.mark.parametrize("board", BOARDS, ids=[board.id for board in BOARDS])
def test_a_full_slot_fits_in_the_macro_region(board: Board) -> None:
    """The same static_assert the firmware makes, on the host side."""
    layout = binary.layout_for_board(board)
    assert board.max_records_per_slot <= layout.record_capacity


@pytest.mark.parametrize("board", BOARDS, ids=[board.id for board in BOARDS])
def test_the_registry_and_the_layout_agree(board: Board) -> None:
    layout = binary.layout_for_board(board)
    assert (layout.size, layout.schema, layout.count_bytes) == (
        board.profile_size,
        board.schema,
        board.count_bytes,
    )
    assert boards.board_by_profile_size(board.profile_size) is board
    assert boards.board_by_id(board.id) is board


@pytest.mark.parametrize("board", BOARDS, ids=[board.id for board in BOARDS])
def test_every_usb_id_resolves_back_to_its_board(board: Board) -> None:
    for vid, pid in board.usb_ids:
        assert boards.board_by_usb_id(vid, pid) is board
    for vid, pid in board.bootloader_ids:
        assert boards.in_bootloader(board, vid, pid)
    for vid, pid in board.run_ids:
        assert not boards.in_bootloader(board, vid, pid)


@pytest.mark.parametrize("board", BOARDS, ids=[board.id for board in BOARDS])
def test_a_board_says_how_it_is_flashed(board: Board) -> None:
    assert board.flash_method in boards.FLASH_METHODS
    assert board.firmware_suffix.startswith(".")
    # Something has to be shown to whoever is holding a board that has never
    # run macroKey firmware, because no software can put it in the bootloader.
    assert board.first_flash_hint.strip(), f"{board.id} has no first-flash hint"


@pytest.mark.parametrize("board", BOARDS, ids=[board.id for board in BOARDS])
def test_a_board_has_a_documentation_page(board: Board) -> None:
    page = ROOT / board.docs_page
    assert page.exists(), f"{board.id} is registered but {board.docs_page} does not exist"
    text = page.read_text("utf-8")
    assert board.id in text, "the page should name the id it documents"


def test_the_index_lists_every_board() -> None:
    """`docs/BOARDS.md` is where someone starts, so a board missing from it is
    a board nobody can find the page for."""
    index = (ROOT / "docs" / "BOARDS.md").read_text("utf-8")
    for board in BOARDS:
        assert f"`{board.id}`" in index, f"{board.id} is not listed in docs/BOARDS.md"


def test_unknown_lookups_are_none_rather_than_a_guess() -> None:
    assert boards.board_by_id("no-such-board") is None
    assert boards.board_by_profile_size(4096) is None
    assert boards.board_by_usb_id(0xCAFE, 0xBEEF) is None
    assert boards.board_by_usb_id(None, None) is None


def test_a_board_cannot_be_registered_half_described() -> None:
    """The dataclass refuses shapes that would fail much later, at flash time."""
    with pytest.raises(ValueError):
        Board(
            id="broken", display_name="", mcu="", fqbn="", core="",
            profile_size=1024, schema=2, count_bytes=1, max_records_per_slot=255,
            run_ids=frozenset(), bootloader_ids=frozenset(),
            flash_method="carrier-pigeon", firmware_suffix=".hex",
        )
    with pytest.raises(ValueError):
        # uf2 means "copy onto the bootloader volume", so there has to be one.
        Board(
            id="broken", display_name="", mcu="", fqbn="", core="",
            profile_size=1024, schema=2, count_bytes=1, max_records_per_slot=255,
            run_ids=frozenset(), bootloader_ids=frozenset(),
            flash_method="uf2", firmware_suffix=".uf2",
        )
