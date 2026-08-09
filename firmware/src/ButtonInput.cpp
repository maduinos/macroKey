#include "ButtonInput.h"

void ButtonInput::begin() {
  for (uint8_t key = 0; key < MK_KEY_COUNT; key++) {
    pinMode(MK_KEY_PINS[key], INPUT_PULLUP);
    // Value-initialised, so a field added to KeyState starts zeroed instead of
    // holding whatever the listed initialisers ran out before reaching.
    keys_[key] = KeyState{};
    keys_[key].phase = PH_IDLE;
  }
}

void ButtonInput::push(uint8_t key, uint8_t gesture, bool released) {
  // A full queue means the engine is not draining; dropping the newest event is
  // better than overwriting one already accepted.
  if (queueCount_ >= QUEUE_SIZE) return;
  uint8_t slot = (uint8_t)((queueHead_ + queueCount_) % QUEUE_SIZE);
  queue_[slot].key = key;
  queue_[slot].gesture = gesture;
  queue_[slot].released = released;
  queueCount_++;
}

bool ButtonInput::nextEvent(KeyEvent *out) {
  if (queueCount_ == 0) return false;
  *out = queue_[queueHead_];
  queueHead_ = (uint8_t)((queueHead_ + 1) % QUEUE_SIZE);
  queueCount_--;
  return true;
}

void ButtonInput::suppressUntilRelease(uint8_t mask) {
  for (uint8_t key = 0; key < MK_KEY_COUNT; key++) {
    if ((mask & (1 << key)) == 0) continue;
    keys_[key].suppressed = true;
    keys_[key].holdFired = true;  // blocks the hold timer from firing later
    keys_[key].phase = PH_DOWN;
  }
}

void ButtonInput::onPressEdge(uint8_t key, uint32_t now) {
  KeyState &state = keys_[key];
  pressedMask_ |= (uint8_t)(1 << key);
  lastPressEdgeAt_ = now;

  // Was this press the second of a quick pair? Answered from the gap alone,
  // deliberately separate from the PH_PENDING_TAP path below: that one only
  // arms for keys that already have a double binding, and recording into the
  // double slot has to work on a key whose double slot is empty. Reading the
  // gap costs nothing and changes no timing.
  state.secondPress =
      state.hasReleased && (uint32_t)(now - state.releasedAt) < MK_DOUBLE_TAP_MS;

  if (state.phase == PH_PENDING_TAP) {
    // Second press inside the window: this is a double tap, and the deferred
    // single tap is discarded rather than emitted first.
    push(key, GESTURE_DOUBLE, false);
    state.phase = PH_DOUBLE_DOWN;
    state.holdFired = true;
  } else {
    state.phase = PH_DOWN;
    state.holdFired = false;
    state.recordFired = false;
  }
  state.pressedAt = now;
}

void ButtonInput::onReleaseEdge(uint8_t key, uint32_t now) {
  KeyState &state = keys_[key];
  pressedMask_ &= (uint8_t)~(1 << key);
  // Every release, not just the ones that defer a tap: the next press reads
  // this to decide whether it is the second of a pair.
  state.releasedAt = now;
  state.hasReleased = true;

  if (state.suppressed) {
    state.suppressed = false;
    state.phase = PH_IDLE;
    return;
  }

  if (state.phase == PH_DOUBLE_DOWN) {
    state.phase = PH_IDLE;
  } else if (state.holdFired) {
    // Tells the engine to unwind a momentary layer or release a held key.
    push(key, GESTURE_HOLD, true);
    state.phase = PH_IDLE;
  } else if (doubleTapMask_ & (1 << key)) {
    state.phase = PH_PENDING_TAP;  // releasedAt was set above
  } else {
    push(key, GESTURE_TAP, false);
    state.phase = PH_IDLE;
  }
}

void ButtonInput::update(uint32_t now) {
  for (uint8_t key = 0; key < MK_KEY_COUNT; key++) {
    KeyState &state = keys_[key];

    bool raw = digitalRead(MK_KEY_PINS[key]) == LOW;
    if (raw != state.rawPressed) {
      state.rawPressed = raw;
      state.changedAt = now;
    } else if (state.stablePressed != raw && now - state.changedAt >= MK_DEBOUNCE_MS) {
      state.stablePressed = raw;
      if (raw) {
        onPressEdge(key, now);
      } else {
        onReleaseEdge(key, now);
      }
    }

    if (state.stablePressed && !state.holdFired &&
        now - state.pressedAt >= MK_HOLD_MS) {
      state.holdFired = true;
      push(key, GESTURE_HOLD, false);
    }

    // Held on its own, for much longer than a hold: the request to start or
    // finish recording. Requiring it to be the only key down is what keeps it
    // out of ordinary use -- holding a key while pressing others is someone
    // using the pad, not someone programming it.
    if (state.stablePressed && !state.recordFired &&
        pressedMask_ == (uint8_t)(1 << key) &&
        now - state.pressedAt >= MK_RECORD_HOLD_MS) {
      state.recordFired = true;
      recordRequest_ = (int8_t)key;
      // Tap-tap-hold programs the double slot, plain hold programs the tap
      // slot. Without this the pad could only ever record into tap, and a
      // double-tap-and-hold -- which already reached here -- stored the macro
      // on the tap slot without saying so.
      recordGesture_ = state.secondPress ? GESTURE_DOUBLE : GESTURE_TAP;
    }

    if (state.phase == PH_PENDING_TAP && now - state.releasedAt >= MK_DOUBLE_TAP_MS) {
      state.phase = PH_IDLE;
      push(key, GESTURE_TAP, false);
    }
  }
}

int8_t ButtonInput::takeRecordRequest() {
  int8_t key = recordRequest_;
  recordRequest_ = -1;
  return key;
}
