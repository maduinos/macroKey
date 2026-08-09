// Debounces the eight GPIO buttons and turns raw levels into gestures.
//
// This is the only module that knows about pins. Swapping to a scanned matrix
// or an I2C expander means replacing this class and nothing above it.
#pragma once

#include <Arduino.h>

#include "ActionTypes.h"
#include "Config.h"

class ButtonInput {
 public:
  void begin();
  void update(uint32_t now);

  // Pops one pending gesture. Returns false when the queue is empty.
  bool nextEvent(KeyEvent *out);

  uint8_t pressedMask() const { return pressedMask_; }
  //: Set when a key has been held alone long enough to mean "program me".
  //: Cleared by reading it, so the engine sees each request exactly once.
  int8_t takeRecordRequest();
  //: Which slot the pending request is for: GESTURE_TAP for a plain hold,
  //: GESTURE_DOUBLE when the held press was the second of a quick pair. Only
  //: meaningful straight after takeRecordRequest returned a key.
  uint8_t recordGesture() const { return recordGesture_; }
  uint32_t lastPressEdgeAt() const { return lastPressEdgeAt_; }

  // Swallows every gesture from these keys until they are released. Used when
  // a record request claims the key, so letting go does not also fire its
  // binding: the person is programming the key, not using it.
  void suppressUntilRelease(uint8_t mask);

  // Keys whose current layer has a DOUBLE binding. Only these pay the double-tap
  // detection delay; every other key emits its tap the instant it is released.
  //
  // This does *not* gate double-tap-and-hold. Recording into the double slot has
  // to work on a key whose double slot is empty -- that is the whole case -- and
  // this mask is, by definition, the keys where it is not. The pair is spotted
  // from the gap between a release and the next press instead, which costs no
  // latency: an unbound key still fires its tap the instant it comes up.
  void setDoubleTapMask(uint8_t mask) { doubleTapMask_ = mask; }

 private:
  enum Phase : uint8_t {
    PH_IDLE = 0,
    PH_DOWN,          // pressed, gesture still undecided
    PH_PENDING_TAP,   // released, waiting out the double-tap window
    PH_DOUBLE_DOWN    // second press of a double, gesture already emitted
  };

  struct KeyState {
    uint32_t changedAt;
    uint32_t pressedAt;
    uint32_t releasedAt;
    uint8_t phase;
    bool rawPressed;
    bool stablePressed;
    bool holdFired;
    bool recordFired;
    bool suppressed;
    //: This press began inside the double-tap window of the last release, so
    //: holding it means "record into the double slot" rather than the tap one.
    bool secondPress;
    //: Whether `releasedAt` means anything yet. Without it the zero it starts
    //: at reads as "released at uptime 0", so for the first MK_DOUBLE_TAP_MS
    //: after boot a key's very first press looked like the second of a pair --
    //: and a plain hold from there programmed the double slot.
    bool hasReleased;
  };

  void push(uint8_t key, uint8_t gesture, bool released);
  void onPressEdge(uint8_t key, uint32_t now);
  void onReleaseEdge(uint8_t key, uint32_t now);

  static const uint8_t QUEUE_SIZE = 12;

  KeyState keys_[MK_KEY_COUNT];
  KeyEvent queue_[QUEUE_SIZE];
  uint8_t queueHead_ = 0;
  uint8_t queueCount_ = 0;
  uint8_t pressedMask_ = 0;
  int8_t recordRequest_ = -1;
  uint8_t recordGesture_ = GESTURE_TAP;
  uint8_t doubleTapMask_ = 0;
  uint32_t lastPressEdgeAt_ = 0;
};
