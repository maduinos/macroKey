"""Every board macroKey runs on, in one table.

Boards differ in four unrelated ways -- pins, storage size, USB identity, and
how firmware gets onto them -- and each of those used to be its own scattered
``if this is the RP2040`` somewhere else in the tree. A third board would have
had to find all four sites, and the one it missed would not fail loudly: an
unknown board silently fell back to the smallest layout, which is how a pad
with room for 21801 records came to report 308.

So the whole description of a board lives here, and everything else derives
from it:

* ``config/binary.py`` builds its profile layouts from ``profile_size`` and
  friends, so the encoder cannot disagree with the registry.
* ``device/discovery.py`` builds its USB id set from ``run_ids`` /
  ``bootloader_ids``, so a new board is recognised on the port list for free.
* ``flash/`` picks the image and the method from ``firmware_suffix`` and
  ``flash_method``.
* ``tests/test_firmware_agreement.py`` walks this table and holds every board
  to the constants its own header declares.

**Adding a board** is then three files: a ``Board`` here, a header in
``firmware/src/boards/``, and a page in ``docs/boards/``. ``docs/BOARDS.md``
carries the checklist, and the tests fail until all three agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: How firmware is written to a board.
#:
#: ``uf2``    -- the bootloader appears as a USB mass-storage volume and the
#:               image is copied onto it. No protocol, no tool, just a file.
#: ``avr109`` -- the bootloader speaks AVR109 (Atmel's "butterfly" protocol)
#:               on a serial port. ``flash/avr109.py`` writes it over pyserial
#:               rather than shelling out to avrdude.
FLASH_METHODS = ("uf2", "avr109")


@dataclass(frozen=True)
class Board:
    """One supported board, described completely.

    ``id`` is the name the firmware reports in ``HELLO board=...``. It is also
    the artifact name and the doc filename, so the three cannot drift.
    """

    id: str
    display_name: str
    mcu: str
    #: What ``arduino-cli compile --fqbn`` is given for this board.
    fqbn: str
    #: The core to install, e.g. ``SparkFun:avr``.
    core: str

    # ------------------------------------------------------------- storage --
    #: Bytes the whole profile blob occupies -- ``MK_PROFILE_SIZE``.
    profile_size: int
    #: Wire schema written into the blob header.
    schema: int
    #: Width of one slot's record count in the macro index.
    count_bytes: int
    #: Ceiling on a single slot -- ``MK_MACRO_MAX_RECORDS``.
    max_records_per_slot: int

    # ----------------------------------------------------------------- usb --
    #: ``(vid, pid)`` pairs the board shows while running a sketch.
    run_ids: frozenset[tuple[int, int]]
    #: ``(vid, pid)`` pairs it shows while sitting in its bootloader.
    bootloader_ids: frozenset[tuple[int, int]]

    # ------------------------------------------------------------ flashing --
    flash_method: str
    #: Filename extension of the built image, including the dot.
    firmware_suffix: str
    #: Board-manager index the core comes from. Empty for cores in the stock
    #: index (``arduino:avr``).
    core_url: str = ""
    #: Volume label the ``uf2`` bootloader mounts under. None for other methods.
    bootloader_volume: str | None = None
    #: What the person has to do by hand the very first time, before any
    #: macroKey firmware exists to be asked politely. Shown in the flashing
    #: dialog; translated through ``i18n`` at the call site.
    first_flash_hint: str = ""
    #: Libraries ``arduino-cli lib install`` needs for this board, on top of
    #: whatever the core itself provides. The RP2040 core ships Keyboard and
    #: Mouse; the AVR core does not, which is the whole difference here.
    libraries: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.flash_method not in FLASH_METHODS:
            raise ValueError(f"{self.id}: unknown flash method {self.flash_method!r}")
        if self.flash_method == "uf2" and not self.bootloader_volume:
            raise ValueError(f"{self.id}: a uf2 board needs a bootloader volume label")

    @property
    def usb_ids(self) -> frozenset[tuple[int, int]]:
        """Everything this board can appear as, running or in its bootloader."""
        return self.run_ids | self.bootloader_ids

    @property
    def select_macro(self) -> str:
        """The ``-D`` that forces this board in a build with no Arduino core.

        ``firmware/src/boards/board.h`` follows the same spelling, and the PC
        test harness uses it to build every board on one machine.
        """
        return "MK_BOARD_" + self.id.upper().replace("-", "_")

    @property
    def firmware_name(self) -> str:
        """The built image's filename, as CI attaches it and the app looks it up."""
        return f"firmware-{self.id}{self.firmware_suffix}"

    @property
    def build_artifact(self) -> str:
        """What arduino-cli leaves in its output directory for this board."""
        return f"firmware.ino{self.firmware_suffix}"

    @property
    def docs_page(self) -> str:
        return f"docs/boards/{self.id}.md"


#: The original hardware: SparkFun Pro Micro / Arduino Micro, ATmega32u4, and
#: the 1 KB of on-chip EEPROM that every size in the AVR layout comes from.
PROMICRO_32U4 = Board(
    id="promicro",
    display_name="Pro Micro (ATmega32u4)",
    mcu="atmega32u4",
    fqbn="SparkFun:avr:promicro:cpu=16MHzatmega32U4",
    core="SparkFun:avr",
    core_url=(
        "https://raw.githubusercontent.com/sparkfun/Arduino_Boards/master/"
        "IDE_Board_Manager/package_sparkfun_index.json"
    ),
    profile_size=1024,
    schema=2,
    count_bytes=1,
    max_records_per_slot=255,
    run_ids=frozenset(
        {
            # Leonardo, Micro, and their Arduino-vendor duplicates.
            *((vendor, product) for vendor in (0x2341, 0x2A03) for product in (0x8036, 0x8037)),
            # SparkFun sketch ids, 3.3 V and 5 V. The shipped pad is the 5 V 9206.
            *((0x1B4F, product) for product in (0x9204, 0x9206)),
        }
    ),
    bootloader_ids=frozenset(
        {
            *((vendor, product) for vendor in (0x2341, 0x2A03) for product in (0x0036, 0x0037)),
            *((0x1B4F, product) for product in (0x9203, 0x9205)),
        }
    ),
    flash_method="avr109",
    firmware_suffix=".hex",
    first_flash_hint=(
        "Short RST to GND twice quickly. The bootloader stays up for 8 seconds."
    ),
    libraries=("Adafruit NeoPixel", "Keyboard", "Mouse"),
)

#: ProMicro RP2040 (16 MB). The profile is a 65520 byte LittleFS image rather
#: than memory-mapped EEPROM, which is where the second count byte and the
#: much larger record ceiling come from.
PROMICRO_RP2040 = Board(
    id="promicro-rp2040",
    display_name="ProMicro RP2040 (16 MB)",
    mcu="rp2040",
    fqbn="rp2040:rp2040:generic:flash=16777216_14680064,freq=133",
    core="rp2040:rp2040",
    core_url=(
        "https://github.com/earlephilhower/arduino-pico/releases/download/"
        "global/package_rp2040_index.json"
    ),
    profile_size=65520,
    schema=3,
    count_bytes=2,
    max_records_per_slot=21800,
    run_ids=frozenset(
        {
            # arduino-pico CDC-only and CDC+HID composites. Which one appears
            # depends on the USB interfaces the sketch enables.
            (0x2E8A, 0x000A),
            (0x2E8A, 0xF009),
            (0x2E8A, 0xF00A),
            (0x2E8A, 0xF10A),
        }
    ),
    #: The BOOTSEL mass-storage device. It is not a serial port at all, so it
    #: never shows up in the port list -- `flash/uf2.py` looks for the volume.
    bootloader_ids=frozenset({(0x2E8A, 0x0003)}),
    flash_method="uf2",
    firmware_suffix=".uf2",
    bootloader_volume="RPI-RP2",
    first_flash_hint=(
        "Hold BOOTSEL while plugging the board in. A drive named RPI-RP2 appears."
    ),
    libraries=("Adafruit NeoPixel",),
)

#: Registration order is display order, and the first entry is what an app that
#: has never seen a device assumes. Keep the smallest board first: guessing low
#: refuses a macro that would have fit, which is recoverable, where guessing
#: high accepts one the pad cannot hold.
BOARDS: tuple[Board, ...] = (PROMICRO_32U4, PROMICRO_RP2040)

DEFAULT_BOARD = BOARDS[0]


def board_by_id(board_id: str) -> Board | None:
    """The board that reports ``board_id`` in HELLO, or None if unknown."""
    for board in BOARDS:
        if board.id == board_id:
            return board
    return None


def board_by_profile_size(profile_size: int) -> Board | None:
    """The board whose blob is ``profile_size`` bytes.

    Sizes are distinct across the registry -- `test_boards.py` holds them so --
    which is what lets a profile blob identify its own board.
    """
    for board in BOARDS:
        if board.profile_size == profile_size:
            return board
    return None


def board_by_usb_id(vid: int | None, pid: int | None) -> Board | None:
    """The board a ``(vid, pid)`` belongs to, running or in its bootloader."""
    if vid is None or pid is None:
        return None
    for board in BOARDS:
        if (vid, pid) in board.usb_ids:
            return board
    return None


def in_bootloader(board: Board, vid: int | None, pid: int | None) -> bool:
    if vid is None or pid is None:
        return False
    return (vid, pid) in board.bootloader_ids


def known_usb_ids() -> frozenset[tuple[int, int]]:
    """Every id any board can appear as. Used to rank the serial port list."""
    ids: set[tuple[int, int]] = set()
    for board in BOARDS:
        ids |= board.usb_ids
    return frozenset(ids)
