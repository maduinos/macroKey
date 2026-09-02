// SparkFun Pro Micro / Arduino Micro -- ATmega32u4, 16 MHz, 5 V.
//
// The original macroKey hardware, and the board every size in the schema 2
// layout comes from. Register: `promicro` in macrokey/boards.py.
// Wiring and flashing: docs/boards/promicro.md.
#pragma once

#define MK_BOARD_NAME "promicro"

// Button pins, active-low with the internal pull-up. Index order is the key
// index reported over serial, so reordering this array remaps the keypad.
static const uint8_t MK_KEY_PINS[MK_KEY_COUNT] = {3, 4, 5, 6, 7, 8, 9, 10};
#define MK_KEY_PINS_DEFINED 1

// WS2812B data pin. The Pro Micro does not break out D11 at all -- D11/D12/D13
// exist on the ATmega32u4 but have no pads on this board. Of what is exposed,
// D2/D3 are SDA/SCL and D14/D15/D16 are SPI, which leaves A0. It sits on the
// same header as VCC and GND, so the LED module wires to one side of the board.
// A0 is digital 18 on the 32u4.
#define MK_LED_PIN 18

// The ATmega32u4's whole on-chip EEPROM. Memory-mapped: a read works from the
// first instruction and a write lands by itself, so begin() and commit() are
// no-ops.
#define MK_EEPROM_SIZE 1024
#define MK_MACRO_COUNT_BYTES 1
// Overridable only so the PC test harness, which has no LittleFS, can
// build this board's layout against the memory-mapped store. An Arduino
// build never defines it, so it always gets the line below.
#ifndef MK_STORAGE_FLASH_EMULATED
#define MK_STORAGE_FLASH_EMULATED 0
#endif

// The established layout. Existing pads in the field hold schema 2 images, so
// this number and its byte offsets do not move.
#define MK_PROFILE_SCHEMA 2

// Records per macro, which on this board is simply as many as a one-byte count
// can address. It was 32 steps, which no real recording met -- a typed command
// line is one 3 byte key action per character and past 32 before it is half
// over -- so recordings fell back to host actions and the pad stopped working
// with the app closed, which is the one thing it exists to do.
#define MK_MACRO_MAX_RECORDS 255

// Caterina, the Leonardo bootloader. Entered by writing a magic word to a
// fixed SRAM address and letting the watchdog reset the part; both halves are
// specific to this bootloader on this architecture, so the code lives in
// SerialProtocol.cpp behind this name.
#define MK_BOOTLOADER_ENTRY_CATERINA 1

// The AVR core builds its HID endpoint with bInterval 1 and offers no way to
// change it, which is why mouse replay always behaved correctly here.
#define MK_HID_POLL_INTERVAL_MS 1
