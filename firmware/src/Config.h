// Build-time configuration for the macroKey firmware.
//
// This header is the single source of truth for pin assignments, counts and
// timings. Changing hardware should mean changing this file and nothing else.
#pragma once

#include <Arduino.h>

#define MK_FIRMWARE_VERSION "0.3.0"
#define MK_PROTOCOL_VERSION 1
#define MK_BOARD_NAME "promicro"

// ---------------------------------------------------------------- topology --

#define MK_KEY_COUNT 8
#define MK_LED_COUNT 1
#define MK_LAYER_COUNT 4
#define MK_GESTURE_COUNT 3
#define MK_CHORD_SLOTS 8
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

// -------------------------------------------------------------- HID backend --

// 0: Keyboard + Mouse from the Arduino AVR core. No extra library, but the
//    consumer page (volume, play/pause) is unavailable and ACT_CONSUMER is a
//    no-op that reports back over serial.
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
#define MK_HOLD_REPEAT_MS 120   // auto-repeat period for repeating hold actions
#define MK_CHORD_WINDOW_MS 40   // presses this close together may form a chord

// HID output is suppressed for this long after boot. If a macro misfires into
// an infinite key storm this window is the only chance to re-flash the board.
#define MK_BOOT_GRACE_MS 2000

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

#define MK_MACRO_MAX_STEPS 32     // per invocation, guards runaway sequences
#define MK_MACRO_MAX_RUN_MS 5000  // total wall clock per invocation
