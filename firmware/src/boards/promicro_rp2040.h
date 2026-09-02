// ProMicro RP2040 (16 MB) -- the arduino-pico core.
//
// Pin-compatible with the 32u4 board in outline but not in numbering, and its
// storage is nothing like it: there is no EEPROM, so the profile is a RAM
// image committed to a LittleFS file. Register: `promicro-rp2040` in
// macrokey/boards.py. Wiring and flashing: docs/boards/promicro-rp2040.md.
#pragma once

#define MK_BOARD_NAME "promicro-rp2040"

// GP2..GP9. Active-low with the internal pull-up, index order is the reported
// key index.
static const uint8_t MK_KEY_PINS[MK_KEY_COUNT] = {2, 3, 4, 5, 6, 7, 8, 9};
#define MK_KEY_PINS_DEFINED 1

// GP21. The onboard NeoPixel of most ProMicro RP2040 boards is on GP25; this
// is the external module macroKey wires, on a free pin next to VCC and GND.
#define MK_LED_PIN 21

// Not a real EEPROM: a RAM buffer that begin() fills from flash and commit()
// writes back through an atomic rename. Sized to leave the profile a round
// 65520 bytes, which is what the host encoder and HELLO both report.
#define MK_EEPROM_SIZE 65520
#define MK_MACRO_COUNT_BYTES 2
// Overridable only so the PC test harness, which has no LittleFS, can
// build this board's layout against the memory-mapped store. An Arduino
// build never defines it, so it always gets the line below.
#ifndef MK_STORAGE_FLASH_EMULATED
#define MK_STORAGE_FLASH_EMULATED 1
#endif

// A larger profile with two-byte macro counts is a different image entirely.
// A pad holding one schema must never interpret the other's bytes, so the
// number changes and a mismatch is rejected and replaced with defaults.
#define MK_PROFILE_SCHEMA 3

// Ample, and bounded by the region rather than by the count width: a slot may
// use the whole macro region. The static_assert in Profile.h holds this at or
// below MK_MACRO_RECORD_CAPACITY.
#define MK_MACRO_MAX_RECORDS 21800

// The RP2040 boot ROM. `reset_usb_boot` drops straight into the BOOTSEL mass
// storage device, which is what lets the desktop app re-flash the pad without
// anyone touching the board.
#define MK_BOOTLOADER_ENTRY_PICOBOOT 1

// How often the host may poll the HID endpoint, in milliseconds.
//
// arduino-pico defaults this to 10 ms:
//
//     int usb_hid_poll_interval __attribute__((weak)) = 10;   // USB.cpp
//
// which is a tenth of what the AVR core's HID endpoint does, and it is the
// ceiling on how fast a replayed mouse movement can be sent. A recorded 50 ms
// slice of motion is replayed as a report per millisecond; at 10 ms per report
// each one waits for the endpoint and the gesture stretches to ten times the
// time it was drawn in. Nothing fails -- the pointer lands in the right place,
// slowly -- so the only symptom is that mouse macros feel wrong on this board
// and felt fine on the 32u4.
//
// The symbol is weak precisely so a sketch can override it; firmware.ino does.
#define MK_HID_POLL_INTERVAL_MS 1
#define MK_HID_POLL_INTERVAL_IS_OVERRIDABLE 1
