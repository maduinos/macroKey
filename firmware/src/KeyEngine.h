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
// showing for as long as the macro lasts -- and a looped macro lasts long
// enough that the link goes quiet too, which a host reads as an unplugged pad.
// The sketch may pump serial from here; `macroRunning()` is what keeps a
// profile commit from landing while a macro is reading EEPROM.
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

  //: True while a macro is replaying, which is to say while `runMacro` is on
  //: the stack. The serial layer reads this to refuse anything that writes:
  //: committing a profile while a macro is reading its records out of EEPROM
  //: would change the steps out from under it.
  bool macroRunning() const { return macroDeadline_ != 0; }

 private:
  void handleEvent(const KeyEvent &event, uint32_t now);
  void refreshDoubleTapMask();

  // Runs one action. `key` is only used for reporting.
  void dispatch(const Action &action, uint8_t key, uint32_t now);
  void dispatchKey(const Action &action);
  // `loops` is how many times to replay the slot; 0 and 1 both mean once.
  void runMacro(uint8_t slot, uint8_t key, uint32_t now, uint8_t loops);
  // Types one text run. Returns the record index just past it.
  uint16_t runText(uint16_t base, uint16_t header, uint8_t length, uint16_t count);
  // Replays a run of consecutive move records at the speed they were recorded
  // at. Returns the record index just past the run, and past the pause it
  // spent moving through.
  uint16_t runMoves(uint16_t base, uint16_t first, uint16_t count);
  // One move, delivered as a mouse would have delivered it: many small reports
  // across `overMs`, rather than the whole distance in a single report.
  void emitMove(int8_t dx, int8_t dy, uint16_t overMs);
  // Keeps the pad alive until `at`. Unlike macroWait this takes an absolute
  // deadline, so time spent inside HID reports comes out of the interval
  // instead of being added to it.
  void pumpUntil(uint32_t at);
  // delay(), but the pad stays awake. When a macro is running, intentional
  // pauses extend the runaway deadline so authored timing is not truncated.
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

  // Non-zero while runMacro is on the stack: millis() deadline for runaway
  // work. macroWait pushes it forward by the pause length, and a looped macro
  // renews it per pass.
  uint32_t macroDeadline_ = 0;

  // Stopping a loop. Only armed when there is more than one pass to stop: a
  // single-run macro keeps queuing presses the way it always has, and changing
  // that would make a short macro un-spammable.
  //
  // Whatever was already held when the macro started does not count. The key
  // that fired it is normally up -- tap and double both fire on release -- but
  // a second key held from before would otherwise stop the loop before its
  // first pass, which is exactly what the replay harness does.
  bool macroAbortArmed_ = false;
  mk_keymask_t macroStartMask_ = 0;
  bool macroAborted_ = false;

  bool hidEnabled_ = false;
};
