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

inline void mkMouseClick(uint8_t mask, uint8_t count) {
  if (count == 0) count = 1;
  for (uint8_t i = 0; i < count; i++) {
    Mouse.click(mask);
    if (i + 1 < count) delay(40);  // double clicks need a gap to register
  }
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
