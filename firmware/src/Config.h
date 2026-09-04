// Build-time configuration for the macroKey firmware.
//
// Everything here is the same on every board: counts, timings, thresholds, and
// the behaviour they describe. What differs from board to board -- pins, the
// size and kind of the store, the wire name, the bootloader -- lives in one
// header per board under boards/, and boards/board.h picks and enforces it.
//
// So a value belongs here if changing the hardware would not change it, and in
// a board header otherwise. Nothing in the firmware outside boards/ tests the
// architecture directly.
#pragma once

#include <Arduino.h>

#define MK_FIRMWARE_VERSION "0.9.4"
#define MK_PROTOCOL_VERSION 1

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

// ------------------------------------------------------------------ board --

// Pins, storage size and kind, wire name, schema, and the per-slot record
// ceiling. Included after the counts above because a board's pin array is
// declared MK_KEY_COUNT long. Everything below this line is board-agnostic
// again.
#include "boards/board.h"

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

// MK_MACRO_MAX_RECORDS -- records one macro may hold -- is a board property
// and is defined in boards/. It is bounded by how wide a slot's stored count
// is, which is why it is not a number that can simply be raised here.
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

// Reports are paced at this multiple of the host's HID poll interval, not at
// the interval itself. Aiming a report at every USB frame leaves no room: the
// frame is the floor, so a report delayed by anything at all -- the pixel's
// bit-bang holding interrupts off, a busier USB device once the editor opens
// the serial port -- has already missed its deadline. The loop cannot catch
// up from an absolute deadline, so the gesture stretches, and a desktop with
// pointer acceleration on multiplies the slower movement by less. The macro
// then lands *short*, by more the faster it was drawn.
//
// Measured: a 50 ms slice used to be 50 reports 1.05 ms apart -- 0.05 ms of
// slack. At 3/2 it is 33 reports 1.5 ms apart, the same distance in the same
// time, with half a millisecond of room per report.
//
// Two constants because this wants to be 1.5 and the arithmetic is integer.
// Raising the numerator buys slack and coarsens the deltas; the ceiling is the
// point where one report is large enough for the acceleration curve to read it
// as a flick, which is the overshoot this whole mechanism exists to remove.
#define MK_MACRO_MOVE_PACE_NUM 3
#define MK_MACRO_MOVE_PACE_DEN 2

// Between characters of a text run. The host needs a report boundary to see
// them as separate keystrokes; below about 4 ms fast applications drop some.
#define MK_MACRO_TEXT_DELAY_MS 5
