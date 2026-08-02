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

void KeyEngine::setReportCallbacks(MkKeyReportFn key, MkHostActionFn host,
                                   MkChordReportFn chord) {
  onKey_ = key;
  onHost_ = host;
  onChord_ = chord;
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
      mkMouseClick(action.a, action.b);
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

void KeyEngine::runMacro(uint8_t slot, uint32_t now) {
  uint8_t steps = profile_->macroStepCount(slot);
  if (steps > MK_MACRO_MAX_STEPS) steps = MK_MACRO_MAX_STEPS;

  for (uint8_t i = 0; i < steps; i++) {
    // A macro runs inline, so both a step count and a wall-clock ceiling guard
    // against a corrupt slot locking the firmware out of its scan loop.
    if (millis() - now > MK_MACRO_MAX_RUN_MS) break;

    MacroStep step = profile_->macroStep(slot, i);
    if (step.type == ACT_DELAY) {
      delay((uint16_t)step.a * 10);
      continue;
    }
    if (step.type == ACT_SEQUENCE) continue;  // no nesting: recursion is a trap
    Action action = {step.type, step.a, step.b, 0};
    dispatch(action, 0, now);
  }
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

  leds_->notePress(event.key, now);
  Action action = profile_->action(layer, event.key, event.gesture);

  // A tap with no binding still falls through to the layer-0 binding, so a
  // half-configured layer behaves like a shifted keypad rather than a dead one.
  if (action.type == ACT_NONE && layer != 0 && event.gesture == GESTURE_TAP) {
    action = profile_->action(0, event.key, GESTURE_TAP);
  }

  dispatch(action, event.key, now);
  if (onKey_ != NULL) onKey_(event.key, event.gesture, layer, false);
}

void KeyEngine::checkChords(uint32_t now) {
  uint8_t pressed = input_->pressedMask();
  if (pressed == 0) {
    chordFiredMask_ = 0;
    return;
  }
  // Two keys must be down, they must have arrived together, and the combination
  // must not have fired already for this press group.
  if (pressed == chordFiredMask_) return;
  if (now - input_->lastPressEdgeAt() > MK_CHORD_WINDOW_MS) return;

  uint8_t mask;
  Action action;
  for (uint8_t slot = 0; slot < MK_CHORD_SLOTS; slot++) {
    if (!profile_->chord(slot, &mask, &action)) continue;
    if (mask != pressed) continue;

    chordFiredMask_ = pressed;
    input_->suppressUntilRelease(pressed);
    dispatch(action, 0, now);
    if (onChord_ != NULL) onChord_(pressed, activeLayer());
    return;
  }
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
  if (!hidEnabled_ && now >= MK_BOOT_GRACE_MS) hidEnabled_ = true;

  checkChords(now);

  KeyEvent event;
  while (input_->nextEvent(&event)) {
    handleEvent(event, now);
  }

  serviceRepeat(now);
  leds_->setActiveLayer(activeLayer());

  if (lastMaskedLayer_ != activeLayer()) refreshDoubleTapMask();
}
