// Thin wrapper over whichever USB HID library is compiled in.
//
// KeyEngine talks only to these functions, so switching between the AVR core's
// Keyboard/Mouse and HID-Project is a one-line change in Config.h.
#pragma once

#include <Arduino.h>

#include "ActionTypes.h"
#include "Config.h"

#if MK_USE_HID_PROJECT
#include <HID-Project.h>
#else
#include <Keyboard.h>
#include <Mouse.h>
#endif

inline void mkHidBegin() {
  Keyboard.begin();
  Mouse.begin();
#if MK_USE_HID_PROJECT
  Consumer.begin();
#endif
}

// Maps our modifier mask onto the library's left/right modifier keycodes.
inline uint8_t mkModifierKeycode(uint8_t bit) {
  switch (bit) {
    case MOD_CTRL: return KEY_LEFT_CTRL;
    case MOD_SHIFT: return KEY_LEFT_SHIFT;
    case MOD_ALT: return KEY_LEFT_ALT;
    case MOD_GUI: return KEY_LEFT_GUI;
    case MOD_RCTRL: return KEY_RIGHT_CTRL;
    case MOD_RSHIFT: return KEY_RIGHT_SHIFT;
    case MOD_RALT: return KEY_RIGHT_ALT;
    case MOD_RGUI: return KEY_RIGHT_GUI;
    default: return 0;
  }
}

inline void mkPressModifiers(uint8_t mask) {
  for (uint8_t bit = 0x01; bit != 0; bit <<= 1) {
    if (mask & bit) Keyboard.press(mkModifierKeycode(bit));
    if (bit == 0x80) break;
  }
}

inline void mkReleaseModifiers(uint8_t mask) {
  for (uint8_t bit = 0x01; bit != 0; bit <<= 1) {
    if (mask & bit) Keyboard.release(mkModifierKeycode(bit));
    if (bit == 0x80) break;
  }
}

inline void mkKeyboardReleaseAll() { Keyboard.releaseAll(); }

inline void mkMouseButton(uint8_t mask, uint8_t mode) {
  switch (mode) {
    case MB_MODE_PRESS: Mouse.press(mask); break;
    case MB_MODE_RELEASE: Mouse.release(mask); break;
    default: Mouse.click(mask); break;
  }
}

// Everything down, unconditionally. A macro cut short between a press and its
// release would otherwise leave the button held with nothing left to let go.
inline void mkMouseReleaseAll() {
  Mouse.release(MB_LEFT);
  Mouse.release(MB_RIGHT);
  Mouse.release(MB_MIDDLE);
}

// Into the top-left corner and stop there. The compositor clamps at the edge,
// so overshooting is how this works rather than something to avoid.
inline void mkMouseHome() {
  for (uint8_t i = 0; i < MK_MOUSE_HOME_STEPS; i++) Mouse.move(-127, -127, 0);
}

// One character of a text run. Printable ASCII only: Keyboard.write maps it to
// a keycode and shift state, and anything else is either a control code we
// never stored or padding at the end of the last record.
inline void mkTypeChar(uint8_t character) {
  if (character < 0x20 || character > 0x7E) return;
  Keyboard.write(character);
}

inline void mkMouseMove(int8_t dx, int8_t dy) { Mouse.move(dx, dy, 0); }

inline void mkMouseWheel(int8_t delta) { Mouse.move(0, 0, delta); }

// Returns false when the build has no consumer page, so the caller can report
// the unsupported action instead of silently doing nothing.
inline bool mkConsumerWrite(uint16_t usage) {
#if MK_USE_HID_PROJECT
  Consumer.write((ConsumerKeycode)usage);
  return true;
#else
  (void)usage;
  return false;
#endif
}
