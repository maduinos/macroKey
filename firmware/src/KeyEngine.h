// Turns gestures into actions.
//
// The engine owns the dispatch table; it does not know about pins
// (ButtonInput) or about the serial link (callbacks below).
#pragma once

#include <Arduino.h>

#include "ActionTypes.h"
#include "ButtonInput.h"
#include "Config.h"
#include "LedController.h"
#include "Profile.h"

// Fired for every gesture, purely informational: the HID report has already
// gone out. The host uses these for logging and LED reactions.
typedef void (*MkKeyReportFn)(uint8_t key, uint8_t gesture, bool released);

//: The pad asking the host to start or finish recording into this key.
//: `gesture` is which slot: GESTURE_TAP for a plain hold, GESTURE_DOUBLE when
//: the held press was the second of a quick pair.
typedef void (*MkRecordRequestFn)(uint8_t key, uint8_t gesture);

// Called while a macro is replaying, roughly every few milliseconds. A macro
// runs inside loop(), so without this the pixel freezes on whatever it was
// showing for as long as the macro lasts. Deliberately not a serial pump: the
// host writes profiles, and letting one land while a macro is reading EEPROM
// would change the steps out from under it.
typedef void (*MkMacroYieldFn)();

class KeyEngine {
 public:
  void begin(Profile *profile, ButtonInput *input, LedController *leds);
  void update(uint32_t now);

  void setRecordCallback(MkRecordRequestFn onRecord) { onRecord_ = onRecord; }
  void setMacroYield(MkMacroYieldFn onYield) { onYield_ = onYield; }

  // The stored profile changed under us. Which keys have a double binding is
  // the one thing the engine caches, and a recording is exactly what changes
  // it: without this a freshly recorded double macro never fired, because the
  // key was still in the "no double, report the tap immediately" set until the
  // pad was next unplugged.
  void noteProfileChanged() { doubleTapMaskReady_ = false; }

  void setReportCallback(MkKeyReportFn key);

  // True once the boot grace window has passed and HID output is allowed.
  bool hidEnabled() const { return hidEnabled_; }

 private:
  void handleEvent(const KeyEvent &event, uint32_t now);
  void serviceRepeat(uint32_t now);
  void refreshDoubleTapMask();

  // Runs one action. `key` is only used for reporting.
  void dispatch(const Action &action, uint8_t key, uint32_t now);
  void dispatchKey(const Action &action);
  void runMacro(uint8_t slot, uint8_t key, uint32_t now);
  // Types one text run. Returns the record index just past it.
  uint8_t runText(uint16_t base, uint8_t header, uint8_t length, uint8_t count);
  // delay(), but the pad stays awake.
  void macroWait(uint16_t milliseconds);
  // One pass of everything that must keep running while a macro blocks loop().
  void macroPump();

  Profile *profile_ = NULL;
  ButtonInput *input_ = NULL;
  LedController *leds_ = NULL;

  MkKeyReportFn onKey_ = NULL;
  MkRecordRequestFn onRecord_ = NULL;
  MkMacroYieldFn onYield_ = NULL;

  //: Whether the double-tap mask has been built yet.
  bool doubleTapMaskReady_ = false;

  // One-shot modifiers armed by a KEYF_STICKY action, consumed by the next key.
  uint8_t stickyModifiers_ = 0;

  // Auto-repeat for a held ACT_KEY with KEYF_REPEAT. One slot: a keypad this
  // size never needs two keys repeating at once, and a table would cost SRAM.
  Action repeatAction_ = {ACT_NONE, 0, 0, 0};
  int8_t repeatKey_ = -1;
  uint32_t repeatNextAt_ = 0;

  bool hidEnabled_ = false;
};
