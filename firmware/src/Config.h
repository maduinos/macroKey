// Build-time configuration for the macroKey firmware.
//
// This header is the single source of truth for pin assignments, counts and
// timings. Changing hardware should mean changing this file and nothing else.
#pragma once

#include <Arduino.h>

#define MK_FIRMWARE_VERSION "0.8.0"
#define MK_PROTOCOL_VERSION 1
#define MK_BOARD_NAME "promicro"

// ---------------------------------------------------------------- topology --

#define MK_KEY_COUNT 8
#define MK_LED_COUNT 1

// A bitmask over the keys, one bit per key index. The scanner's pressed,
// double-tap and suppress masks are all this type, so raising MK_KEY_COUNT past
// its width is a compile error here rather than eight keys that work and the
// rest silently doing nothing -- which is what a wider board would have got.
typedef uint8_t mk_keymask_t;
static_assert(MK_KEY_COUNT <= (int)(sizeof(mk_keymask_t) * 8),
              "MK_KEY_COUNT exceeds the key mask width: widen mk_keymask_t");
// No layers. Eight keys that each do one thing is the whole product; layers
// added a mode nobody could see and a shortcut nobody could remember, and the
// keymap they cost is EEPROM that macro records use instead. What survived the
// first cut was a `layer` argument on every call that was always zero.
// Gestures the scanner can report. Hold is one of them, but it is not stored:
// see MK_KEYMAP_GESTURES.
#define MK_GESTURE_COUNT 3
// Gestures the keymap has room for, and the order they sit in: tap, then
// double. Hold is how recording starts, so nothing may be bound to it and its
// slot held nothing but zeroes on every key -- 32 bytes macro records now use.
#define MK_KEYMAP_GESTURES 2
// No chords. Eight slots at five bytes that nothing could fill: the editor
// never offered them and the defaults left them empty, so the region only ever
// held zeroes. Forty more bytes for macro records.
#define MK_MACRO_SLOTS 16

// Button pins, active-low with the internal pull-up. Index order is the key
// index reported over serial, so reordering this array remaps the keypad.
static const uint8_t MK_KEY_PINS[MK_KEY_COUNT] = {3, 4, 5, 6, 7, 8, 9, 10};

// WS2812B data pin. The Pro Micro does not break out D11 at all -- D11/D12/D13
// exist on the ATmega32u4 but have no pads on this board. Of what is exposed,
// D2/D3 are SDA/SCL and D14/D15/D16 are SPI, which leaves A0. It sits on the
// same header as VCC and GND, so the LED module wires to one side of the board.
// A0 is digital 18 on the 32u4.
#define MK_LED_PIN 18

// ---------------------------------------------------------------- storage --

// Bytes the profile gets. The ATmega32u4 has exactly this much EEPROM and the
// profile is the only thing in it, so the two numbers are the same one. It
// lives here rather than in Profile.h because it is a fact about the board:
// a part with more room changes this line and the macro region grows into it.
#define MK_EEPROM_SIZE 1024

// 0: AVR-style EEPROM. Memory-mapped, every write lands on its own, and reads
//    work from the first instruction -- so begin() and commit() are no-ops.
// 1: flash-emulated EEPROM (RP2040, ESP32). The library holds a RAM copy that
//    must be filled by begin() before the first read and written back by
//    commit() after the last write. Without both, reads return rubbish and
//    nothing survives a power cycle.
// Profile only ever goes through Storage.h, so this is the whole switch.
#ifndef MK_STORAGE_FLASH_EMULATED
#define MK_STORAGE_FLASH_EMULATED 0
#endif

// -------------------------------------------------------------- HID backend --

// 0: Keyboard + Mouse from the Arduino AVR core. No extra library, but the
//    consumer page (volume, play/pause) is unavailable, so ACT_CONSUMER does
//    nothing at all.
// 1: NicoHood's HID-Project, which adds the consumer page. Install with
//    `arduino-cli lib install "HID-Project"`.
#ifndef MK_USE_HID_PROJECT
#define MK_USE_HID_PROJECT 0
#endif

// ----------------------------------------------------------------- timings --

#define MK_SCAN_INTERVAL_MS 2   // button poll period
#define MK_DEBOUNCE_MS 12       // stable window before a level change counts
#define MK_DOUBLE_TAP_MS 250    // second press must start within this window
#define MK_HOLD_MS 400          // press longer than this becomes a hold

// HID output is suppressed for this long after boot. If a macro misfires into
// an infinite key storm this window is the only chance to re-flash the board.
#define MK_BOOT_GRACE_MS 2000

// A key held this long on its own asks the host to start or finish recording.
// "On its own" is what keeps it from firing during ordinary use: holding a key
// while pressing others is someone using the pad, not someone programming it.
#define MK_RECORD_HOLD_MS 3000

// --------------------------------------------------------------------- LED --

#define MK_LED_FPS 50            // WS2812 bit-banging blocks interrupts; cap it
// A single pixel tops out near 60 mA, so against the 500 mA USB budget the
// limiter can never actually engage. It stays in the build because it is the
// thing that keeps MK_LED_COUNT safe to raise: bump the count and this ceiling
// starts doing real work without any other change.
#define MK_LED_MAX_MILLIAMPS 100
#define MK_LED_DEFAULT_BRIGHTNESS 64
#define MK_LED_PRESS_FLASH_MS 120

// A scene change cross-fades over this long instead of snapping. The pad lives
// in peripheral vision, where it is the instant jump that catches the eye, not
// the colour itself: fading in over half a second reads as "something changed"
// without pulling a look away from the screen. Set to 0 for instant switching.
#define MK_LED_FADE_MS 500

// How far a key press lifts the pixel toward white, 0-255. At 255 the press is
// a hard white punch that overrides the ambient colour completely; lower values
// read as the current colour brightening, so feedback and status coexist.
#define MK_LED_PRESS_FLASH_AMOUNT 150

// Crossing the hold threshold gets its own cue, brighter and longer than a tap.
// MK_HOLD_MS is 400 ms of nothing happening otherwise: the press flash has long
// faded and the action has not fired yet, so there is no way to tell a hold that
// registered from one that did not until after letting go. This says "let go now
// and it counts" while the finger is still down.
#define MK_LED_HOLD_FLASH_MS 220
#define MK_LED_HOLD_FLASH_AMOUNT 255

// A key with nothing bound on this layer does nothing at all, which looks the
// same as a dead board or a missed press. A dim red says the press was received
// and there was simply nothing to run.
#define MK_LED_UNBOUND_FLASH_MS 180
#define MK_LED_UNBOUND_FLASH_AMOUNT 255

// Sequence macros block loop() for as long as they run. The busy colour says
// "still working"; the done flash says "finished" so a long typed line is not
// mistaken for a stuck pad. Distinct from recording red and the unbound dim red.
#define MK_LED_MACRO_BUSY_PERIOD_MS 500
#define MK_LED_MACRO_BUSY_AMOUNT 200
#define MK_LED_MACRO_DONE_MS 350
#define MK_LED_MACRO_DONE_AMOUNT 230

// Host ambient control lapses back to the local scene after this much silence,
// so closing the desktop app never freezes the strip on its last colour. A host
// that knows it will be quiet for longer -- one sitting on a colour picker, or
// writing a profile, which takes several seconds -- can raise its own deadline
// with `LED mode=host ms=<n>` instead of sending keepalives it has no reason to
// send. The ceiling keeps a crashed host from parking the pixel indefinitely.
#define MK_LED_HOST_TIMEOUT_MS 3000
#define MK_LED_HOST_TIMEOUT_MAX_MS 60000

// The board's own RX/TX LEDs are not part of the status display: the USB core
// pulses them on every CDC transfer, so an idle pad still blinks whenever the
// desktop probes the port. Holding them off makes the WS2812 the only light on
// the pad. Set to 0 to get the stock blink back while debugging serial traffic.
// The power LED is tied straight to VCC and cannot be reached from firmware.
#define MK_QUIET_BOARD_LEDS 1

// ------------------------------------------------------------------ serial --

#define MK_SERIAL_BAUD 115200
#define MK_LINE_MAX 96
#define MK_PROFILE_STAGE_TIMEOUT_MS 5000

// ------------------------------------------------------------------ limits --

// Records per invocation, guarding a runaway sequence rather than rationing
// storage: a slot's count is one byte, so this is simply as many records as a
// macro can have. It was 32 steps, which no real recording met -- a typed
// command line was one 3 byte key action per character and past 32 before it
// was half over -- so recordings fell back to host actions and the pad stopped
// working with the app closed, which is the one thing it exists to do.
#define MK_MACRO_MAX_RECORDS 255
// Budget for HID *work* inside one macro (moves, clicks, keys), not for
// authored pauses. ACT_DELAY / text char waits extend the deadline via
// macroWait -- otherwise a drag with thinking-time pauses dies before its
// final Esc, which is exactly what a real recording looked like. 10 s of
// continuous work still stops a corrupt slot spinning forever; a plain 30 s
// wall clock used to make the desktop app decide the link was dead.
#define MK_MACRO_MAX_RUN_MS 10000
// How far ACT_MOUSE_HOME pushes, in steps of 127 raw units on each axis. The
// pointer stops at the edge, so this only has to be further than the desktop is
// wide. 128 covers 16256 raw units, including two 8K-wide displays; the old
// 6096-unit push stopped short on ordinary dual-4K horizontal layouts.
#define MK_MOUSE_HOME_STEPS 128

// A recorded pointer move is replayed spread across the pause that follows it,
// so it travels at the speed it was made at instead of arriving as one jump.
// This caps how much of that pause may be spent moving.
//
// The host slices raw motion into 50 ms pieces and flushes a resting pointer
// after 120 ms, so anything past this is not a gesture that was still moving --
// it is the person having stopped. Spreading a move across *that* would make
// the pointer crawl for the length of a pause it was never moving through.
#define MK_MACRO_MOVE_SPREAD_MAX_MS 150

// How long one recorded move record represents. The host sums raw motion into
// pieces this long (MOTION_SLICE_SECONDS in macrokey/recorder/evdev_source.py)
// and the firmware agreement test holds the two to the same number.
//
// It matters for the last move of a recording, which has no pause after it to
// read the duration from. It is still one slice of motion, so replaying it as
// a single jump would put the overshoot this whole mechanism removes back on
// the end of every drag -- which is exactly where a drag is aimed.
#define MK_MACRO_MOVE_SLICE_MS 50

// Between characters of a text run. The host needs a report boundary to see
// them as separate keystrokes; below about 4 ms fast applications drop some.
#define MK_MACRO_TEXT_DELAY_MS 5
