// The action vocabulary shared by the profile format, the key engine and the
// host protocol. Adding a capability means adding an enum value here plus one
// entry in the KeyEngine dispatch table -- never a new branch in the scanner.
#pragma once

#include <Arduino.h>

enum ActionType : uint8_t {
  ACT_NONE = 0,
  ACT_KEY,             // a = modifier mask, b = keycode, c = flags
  ACT_CONSUMER,        // a = usage low, b = usage high
  ACT_MOUSE_BUTTON,    // a = button mask, b = MouseMode
  ACT_MOUSE_MOVE,      // a = dx (int8), b = dy (int8), c = repeat
  ACT_MOUSE_WHEEL,     // a = delta (int8), c = repeat
  ACT_LAYER_MOMENTARY, // a = layer
  ACT_LAYER_TOGGLE,    // a = layer
  ACT_SEQUENCE,        // a = macro slot
  ACT_HOST,            // a = host token id, executed by the desktop app
  ACT_LED_SCENE,       // a = palette/scene index
  ACT_DELAY,           // a = delay in 10 ms units (macro records only)
  // a = character count. The characters follow, packed three to a record, so a
  // run costs about a byte a letter instead of a whole 3 byte key action each.
  // Macro records only -- it spans more than one and so cannot sit in a keymap
  // slot, which is a fixed four bytes.
  ACT_TEXT,
  ACT_TYPE_COUNT
};

// ACT_MOUSE_BUTTON's `b`. A press that is never released is what a drag is made
// of, so it is a separate mode rather than a click with the release implied.
enum MouseMode : uint8_t {
  MB_MODE_CLICK = 0,
  MB_MODE_PRESS = 1,
  MB_MODE_RELEASE = 2
};

// Modifier mask for ACT_KEY. Matches the USB HID modifier byte layout.
enum KeyModifier : uint8_t {
  MOD_NONE = 0x00,
  MOD_CTRL = 0x01,
  MOD_SHIFT = 0x02,
  MOD_ALT = 0x04,
  MOD_GUI = 0x08,
  MOD_RCTRL = 0x10,
  MOD_RSHIFT = 0x20,
  MOD_RALT = 0x40,
  MOD_RGUI = 0x80
};

// Flags byte (Action::c) for ACT_KEY.
enum KeyActionFlag : uint8_t {
  KEYF_NONE = 0x00,
  KEYF_REPEAT = 0x01,  // auto-repeat while held
  KEYF_STICKY = 0x02   // one-shot modifier: stays armed until the next key
};

enum MouseButtonMask : uint8_t {
  MB_LEFT = 0x01,
  MB_RIGHT = 0x02,
  MB_MIDDLE = 0x04
};

// One profile slot. Fixed at four bytes so the EEPROM layout stays a flat
// indexable array; variable-length encodings would cost a parser and
// fragmentation that a 1 KB EEPROM cannot afford.
struct Action {
  uint8_t type;
  uint8_t a;
  uint8_t b;
  uint8_t c;
};

enum Gesture : uint8_t {
  GESTURE_TAP = 0,
  GESTURE_DOUBLE = 1,
  GESTURE_HOLD = 2
};

// Emitted by ButtonInput, consumed by KeyEngine.
struct KeyEvent {
  uint8_t key;
  uint8_t gesture;
  bool released;  // true when a hold ends, so momentary layers can unwind
};

inline bool actionIsLayer(uint8_t type) {
  return type == ACT_LAYER_MOMENTARY || type == ACT_LAYER_TOGGLE;
}

inline const char *gestureName(uint8_t gesture) {
  switch (gesture) {
    case GESTURE_TAP: return "tap";
    case GESTURE_DOUBLE: return "double";
    case GESTURE_HOLD: return "hold";
    default: return "?";
  }
}
