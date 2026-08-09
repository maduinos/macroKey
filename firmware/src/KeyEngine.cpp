#include "KeyEngine.h"

#include "HidBackend.h"

void KeyEngine::begin(Profile *profile, ButtonInput *input, LedController *leds) {
  profile_ = profile;
  input_ = input;
  leds_ = leds;
  baseLayer_ = profile->baseLayer();
  momentaryKey_ = -1;
  mkHidBegin();
  refreshDoubleTapMask();
}

void KeyEngine::setReportCallbacks(MkKeyReportFn key, MkHostActionFn host) {
  onKey_ = key;
  onHost_ = host;
}

uint8_t KeyEngine::activeLayer() const {
  return momentaryKey_ >= 0 ? momentaryLayer_ : baseLayer_;
}

void KeyEngine::setBaseLayer(uint8_t layer) {
  if (layer >= MK_LAYER_COUNT) return;
  baseLayer_ = layer;
}

// Only keys that actually have a double-tap binding on the current layer pay
// the detection delay; everything else stays instant on release.
void KeyEngine::refreshDoubleTapMask() {
  uint8_t layer = activeLayer();
  uint8_t mask = 0;
  for (uint8_t key = 0; key < MK_KEY_COUNT; key++) {
    if (profile_->action(layer, key, GESTURE_DOUBLE).type != ACT_NONE) {
      mask |= (uint8_t)(1 << key);
    }
  }
  input_->setDoubleTapMask(mask);
  lastMaskedLayer_ = layer;
}

void KeyEngine::dispatchKey(const Action &action) {
  uint8_t modifiers = (uint8_t)(action.a | stickyModifiers_);
  stickyModifiers_ = 0;

  if (action.c & KEYF_STICKY) {
    // A sticky action arms modifiers instead of typing anything now.
    stickyModifiers_ = action.a;
    return;
  }

  mkPressModifiers(modifiers);
  if (action.b != 0) {
    Keyboard.press(action.b);
    delay(8);  // give the host a report boundary between press and release
    Keyboard.release(action.b);
  }
  mkReleaseModifiers(modifiers);
}

void KeyEngine::dispatch(const Action &action, uint8_t key, uint32_t now) {
  if (!hidEnabled_ && action.type != ACT_HOST && action.type != ACT_LAYER_MOMENTARY &&
      action.type != ACT_LAYER_TOGGLE) {
    return;  // boot grace window: no HID output yet
  }

  switch (action.type) {
    case ACT_KEY:
      dispatchKey(action);
      if (action.c & KEYF_REPEAT) {
        repeatAction_ = action;
        repeatKey_ = (int8_t)key;
        repeatNextAt_ = now + MK_HOLD_REPEAT_MS;
      }
      break;

    case ACT_CONSUMER:
      mkConsumerWrite((uint16_t)action.a | ((uint16_t)action.b << 8));
      break;

    case ACT_MOUSE_BUTTON:
      mkMouseButton(action.a, action.b);
      break;

    case ACT_MOUSE_MOVE:
      mkMouseMove((int8_t)action.a, (int8_t)action.b);
      break;

    case ACT_MOUSE_WHEEL:
      mkMouseWheel((int8_t)action.a);
      break;

    case ACT_LAYER_MOMENTARY:
      if (action.a < MK_LAYER_COUNT) {
        momentaryLayer_ = action.a;
        momentaryKey_ = (int8_t)key;
        refreshDoubleTapMask();
      }
      break;

    case ACT_LAYER_TOGGLE:
      if (action.a < MK_LAYER_COUNT) {
        baseLayer_ = baseLayer_ == action.a ? 0 : action.a;
        refreshDoubleTapMask();
      }
      break;

    case ACT_SEQUENCE:
      runMacro(action.a, now);
      break;

    case ACT_HOST:
      if (onHost_ != NULL) onHost_(action.a, key, activeLayer());
      break;

    case ACT_LED_SCENE:
      leds_->setHostMode(false, now);
      break;

    case ACT_NONE:
    default:
      break;
  }
}

void KeyEngine::macroWait(uint16_t milliseconds) {
  // delay() with the pixel kept alive. A macro runs inside loop(), so a plain
  // delay freezes the LED for as long as the macro lasts -- and replaying real
  // thinking-time is the point, so that can be seconds of a pad that looks
  // dead in the middle of doing exactly what was asked.
  if (onYield_ == NULL) {
    delay(milliseconds);  // nothing to pump; let the core's delay do it
    return;
  }
  uint32_t until = millis() + milliseconds;
  while ((int32_t)(millis() - until) < 0) {
    onYield_();
  }
}

uint8_t KeyEngine::runText(uint16_t base, uint8_t header, uint8_t length, uint8_t count) {
  uint8_t payload = (uint8_t)((length + 2) / 3);  // three characters per record
  uint8_t next = (uint8_t)(header + 1 + payload);
  // A run whose characters were cut off by the end of the slot. Typing what is
  // there would spray whatever the neighbouring records happen to hold.
  if (next > count) return count;

  for (uint8_t record = 0; record < payload; record++) {
    MacroStep packed = profile_->macroRecord(base, (uint8_t)(header + 1 + record));
    uint8_t bytes[3] = {packed.type, packed.a, packed.b};
    for (uint8_t offset = 0; offset < 3; offset++) {
      uint8_t index = (uint8_t)(record * 3 + offset);
      if (index >= length) break;
      mkTypeChar(bytes[offset]);
      macroWait(MK_MACRO_TEXT_DELAY_MS);
    }
  }
  return next;
}

void KeyEngine::runMacro(uint8_t slot, uint32_t now) {
  // No clamp against MK_MACRO_MAX_RECORDS: the count is one byte and the limit
  // is 255, which Profile.h asserts. Reading past the region is what actually
  // needs guarding, and macroRecord does that per record.
  uint8_t count = profile_->macroRecordCount(slot);
  uint16_t base = profile_->macroBase(slot);

  uint8_t index = 0;
  while (index < count) {
    // A macro runs inline, so both a record count and a wall-clock ceiling
    // guard against a corrupt slot locking the firmware out of its scan loop.
    if (millis() - now > MK_MACRO_MAX_RUN_MS) break;

    MacroStep record = profile_->macroRecord(base, index);

    if (record.type == ACT_TEXT) {
      index = runText(base, index, record.a, count);
      continue;
    }
    index++;
    if (record.type == ACT_DELAY) {
      macroWait((uint16_t)record.a * 10);
      continue;
    }
    if (record.type == ACT_SEQUENCE) continue;  // no nesting: recursion is a trap
    if (record.type >= ACT_TYPE_COUNT) continue;

    Action action = {record.type, record.a, record.b, 0};
    dispatch(action, 0, now);
  }

  // Whatever the macro was holding goes back up. A recording is allowed to
  // press a mouse button and move before releasing it -- that is what a drag
  // is -- so a macro cut short by the ceiling above could otherwise leave the
  // button down with nothing left to run that would let go of it.
  mkMouseReleaseAll();
  mkKeyboardReleaseAll();
}

void KeyEngine::handleEvent(const KeyEvent &event, uint32_t now) {
  uint8_t layer = activeLayer();

  if (event.released) {
    // End of a hold. Unwind a momentary layer and stop any auto-repeat.
    if (momentaryKey_ == (int8_t)event.key) {
      momentaryKey_ = -1;
      refreshDoubleTapMask();
    }
    if (repeatKey_ == (int8_t)event.key) {
      repeatKey_ = -1;
      repeatAction_.type = ACT_NONE;
    }
    if (onKey_ != NULL) onKey_(event.key, event.gesture, layer, true);
    return;
  }

  // A hold announces itself the moment the threshold is crossed, which is while
  // the finger is still down. That is the only point where the cue can still
  // change what the person does -- after the release it is just history.
  if (event.gesture == GESTURE_HOLD) {
    // Every hold is on its way to the recorder, and none of them is bound to
    // anything. Saying "nothing here" at 400 ms, which is what the unbound cue
    // below would do, reads as a rejected press at exactly the moment the
    // person is being asked to keep holding. This is the cue that means
    // "registered, keep going".
    leds_->noteHold(event.key, now);
    if (onKey_ != NULL) onKey_(event.key, event.gesture, layer, false);
    return;
  }
  leds_->notePress(event.key, now);

  Action action = profile_->action(layer, event.key, event.gesture);

  if (action.type == ACT_NONE) {
    leds_->noteUnbound(event.key, now);
  }

  dispatch(action, event.key, now);
  if (onKey_ != NULL) onKey_(event.key, event.gesture, layer, false);
}

void KeyEngine::serviceRepeat(uint32_t now) {
  if (repeatKey_ < 0 || repeatAction_.type != ACT_KEY) return;
  if ((int32_t)(now - repeatNextAt_) < 0) return;
  repeatNextAt_ = now + MK_HOLD_REPEAT_MS;
  Action once = repeatAction_;
  once.c = (uint8_t)(once.c & ~KEYF_REPEAT);  // avoid re-arming on every tick
  dispatchKey(once);
}

void KeyEngine::update(uint32_t now) {
  // Asked for before anything else this pass, so the request is reported even
  // if the same tick also produces ordinary key events.
  int8_t recordKey = input_->takeRecordRequest();
  if (recordKey >= 0 && onRecord_ != NULL) {
    // The key is still down. Suppressing it stops the release from firing the
    // binding as well: the person is programming the key, not using it.
    input_->suppressUntilRelease((uint8_t)(1 << recordKey));
    onRecord_((uint8_t)recordKey);
  }

  if (!hidEnabled_ && now >= MK_BOOT_GRACE_MS) hidEnabled_ = true;

  KeyEvent event;
  while (input_->nextEvent(&event)) {
    handleEvent(event, now);
  }

  serviceRepeat(now);
  leds_->setActiveLayer(activeLayer());

  if (lastMaskedLayer_ != activeLayer()) refreshDoubleTapMask();
}
