// Board selection, and the contract every board header has to meet.
//
// A board differs from another in four unrelated ways -- its pins, how much
// storage it has and how that storage behaves, what it is called on the wire,
// and how it gets into its bootloader. Those used to be six separate
// `#if defined(ARDUINO_ARCH_RP2040)` blocks scattered through Config.h,
// Profile.h, Storage.cpp and SerialProtocol.cpp. Adding a third board meant
// finding all six, and the one that was missed would not fail the build: it
// would silently take the AVR arm and produce a pad that works until it runs
// out of a kilobyte it does not have.
//
// So a board is one header now, and this file picks it and then checks that it
// actually said everything. A missing symbol is a compile error naming the
// symbol, which is the failure mode a new board should get.
//
// The selection can be forced with -DMK_BOARD_<ID>, which is how the PC test
// harness builds every board on one machine; otherwise it follows the core's
// own architecture macro.
#pragma once

#include <Arduino.h>

#if !defined(MK_BOARD_PROMICRO) && !defined(MK_BOARD_PROMICRO_RP2040)
#if defined(ARDUINO_ARCH_RP2040)
#define MK_BOARD_PROMICRO_RP2040 1
#elif defined(ARDUINO_ARCH_AVR)
#define MK_BOARD_PROMICRO 1
#else
#error "No board selected and the architecture is not one macroKey knows. \
Add a header in firmware/src/boards/, select it here, and register the board \
in macrokey/boards.py -- docs/BOARDS.md has the checklist."
#endif
#endif

#if defined(MK_BOARD_PROMICRO_RP2040)
#include "promicro_rp2040.h"
#elif defined(MK_BOARD_PROMICRO)
#include "promicro_32u4.h"
#endif

// --------------------------------------------------------------- contract --
//
// Everything a board must declare. Kept as errors rather than defaults on
// purpose: a plausible default for storage size or a pin list is exactly the
// kind of thing that produces a board which builds, boots, and is wrong.

#ifndef MK_BOARD_NAME
#error "the board header must define MK_BOARD_NAME (it is what HELLO reports, \
and must match the Board.id registered in macrokey/boards.py)"
#endif
#ifndef MK_KEY_PINS_DEFINED
#error "the board header must define MK_KEY_PINS[MK_KEY_COUNT] and then \
MK_KEY_PINS_DEFINED"
#endif
#ifndef MK_LED_PIN
#error "the board header must define MK_LED_PIN"
#endif
#ifndef MK_EEPROM_SIZE
#error "the board header must define MK_EEPROM_SIZE (the whole profile blob)"
#endif
#ifndef MK_MACRO_COUNT_BYTES
#error "the board header must define MK_MACRO_COUNT_BYTES (1 or 2)"
#endif
#ifndef MK_PROFILE_SCHEMA
#error "the board header must define MK_PROFILE_SCHEMA"
#endif
#ifndef MK_MACRO_MAX_RECORDS
#error "the board header must define MK_MACRO_MAX_RECORDS"
#endif
#ifndef MK_HID_POLL_INTERVAL_MS
#error "the board header must define MK_HID_POLL_INTERVAL_MS (how often the host \
may poll the HID endpoint; it is the ceiling on mouse replay speed)"
#endif
#ifndef MK_STORAGE_FLASH_EMULATED
#error "the board header must define MK_STORAGE_FLASH_EMULATED (0 memory-mapped \
EEPROM, 1 RAM image committed to flash)"
#endif

// A one-byte count cannot address more than 255 records, and the mismatch is
// invisible: the index writes the low byte and the pad replays a fraction of
// the macro. Caught here, once, for every board.
#if MK_MACRO_COUNT_BYTES == 1 && MK_MACRO_MAX_RECORDS > 255
#error "MK_MACRO_MAX_RECORDS does not fit in a one-byte slot count"
#endif
#if MK_MACRO_COUNT_BYTES != 1 && MK_MACRO_COUNT_BYTES != 2
#error "MK_MACRO_COUNT_BYTES must be 1 or 2"
#endif
