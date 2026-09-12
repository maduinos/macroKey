#include "KeyEngine.h"

#include "HidBackend.h"

void KeyEngine::begin(Profile *profile, ButtonInput *input, LedController *leds) {
  profile_ = profile;
  input_ = input;
  leds_ = leds;
  mkHidBegin();
  refreshDoubleTapMask();
}

void KeyEngine::setReportCallback(MkKeyReportFn key) {
  onKey_ = key;
}

// Only keys that actually have a double-tap binding pay the detection delay;
// everything else stays instant on release.
void KeyEngine::refreshDoubleTapMask() {
  mk_keymask_t mask = 0;
  for (uint8_t key = 0; key < MK_KEY_COUNT; key++) {
    if (profile_->action(key, GESTURE_DOUBLE).type != ACT_NONE) {
      mask |= (mk_keymask_t)((mk_keymask_t)1 << key);
    }
  }
  input_->setDoubleTapMask(mask);
  doubleTapMaskReady_ = true;
}

void KeyEngine::dispatchKey(const Action &action) {
  uint8_t modifiers = (uint8_t)(action.a | stickyModifiers_);
  stickyModifiers_ = 0;

  if (action.c & KEYF_STICKY) {
    // A sticky action arms modifiers instead of typing anything now.
    stickyModifiers_ = action.a;
    return;
  }

  // Press and release are the two halves of a hold. A recording of Shift+W
  // held for seconds is press, then authored delays, then release -- the same
  // shape as a mouse drag. Leaving either half out is how a key gets stuck,
  // and runMacro's Keyboard.releaseAll at the end is the safety net.
  if (action.type == ACT_KEY_PRESS) {
    mkPressModifiers(modifiers);
    if (action.b != 0) Keyboard.press(action.b);
    return;
  }
  if (action.type == ACT_KEY_RELEASE) {
    if (action.b != 0) Keyboard.release(action.b);
    mkReleaseModifiers(modifiers);
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
  // Boot grace window: no HID output yet.
  if (!hidEnabled_) return;

  switch (action.type) {
    case ACT_KEY:
    case ACT_KEY_PRESS:
    case ACT_KEY_RELEASE:
      dispatchKey(action);
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

    case ACT_MOUSE_HOME:
      mkMouseHome();
      break;

    case ACT_SEQUENCE:
      runMacro(action.a, key, now, action.b);
      break;

    case ACT_LED_SCENE:
      // Back to the profile's colour. `action.a` is reserved: one palette entry
      // per pixel means there is no second scene to select.
      leds_->setHostMode(false, now);
      break;

    case ACT_RESERVED_9:
      // Former ACT_HOST: pad is HID-only; do nothing.
      break;

    case ACT_NONE:
    default:
      break;
  }
}

void KeyEngine::macroWait(uint16_t milliseconds) {
  // delay(), but the pad stays awake. A macro runs inside loop(), so a plain
  // delay stops everything for as long as the macro lasts -- and replaying real
  // thinking-time is the point, so that can be seconds.
  //
  // The button scan is the part that must not stop, and it is not obvious why.
  // Recording into the double slot means tapping a key and pressing it again
  // inside MK_DOUBLE_TAP_MS. That first tap fires whatever the key is bound to;
  // if that is a macro, the scan used to stop until the macro finished, so the
  // second press was timestamped seconds late and read as an ordinary hold.
  // Double recording was therefore impossible on exactly the keys that already
  // had a macro on them -- which is every key worth re-recording.
  //
  // `onYield_` is the rest: the pixel, the link, and whatever else the sketch
  // wants kept alive. Serial is in it, but only far enough to answer a status
  // query -- the protocol refuses anything that writes while a macro is on the
  // stack, because committing a profile while this macro is reading its records
  // out of EEPROM would change the steps out from under it.
  //
  // Authored pauses do not spend the runaway budget: MK_MACRO_MAX_RUN_MS is for
  // a corrupt slot that never yields, not for "wait 2 s then press Esc".
  if (macroDeadline_ != 0) {
    macroDeadline_ += milliseconds;
  }
  uint32_t until = millis() + milliseconds;
  while ((int32_t)(millis() - until) < 0) {
    macroPump();
    // A stopped loop must not sit out the rest of a recorded pause. Holding a
    // key for ten seconds is one ACT_DELAY chain, and waiting it out would mean
    // pressing stop and watching nothing happen for most of a pass.
    if (macroAborted_) return;
  }
}

void KeyEngine::macroPump() {
  input_->update(millis());

  // Any pad key pressed since the loop started stops it. The scan is already
  // running through a macro (see macroWait), so this costs a mask compare.
  //
  // Read from `pressedMask` rather than the gesture queue on purpose: a queued
  // gesture fires on release, so by the time it arrived the loop would have run
  // on for however long the key was held. The mask is the press itself.
  if (macroAbortArmed_ && !macroAborted_) {
    mk_keymask_t pressed = (mk_keymask_t)(input_->pressedMask() & ~macroStartMask_);
    if (pressed != 0) {
      macroAborted_ = true;
      // The key that stopped the loop must not then do whatever it is bound to.
      // This is the same swallow a record request uses when it claims a key.
      input_->suppressUntilRelease(pressed);
    }
  }

  if (onYield_ != NULL) onYield_();
}

uint16_t KeyEngine::runText(
    uint16_t base, uint16_t header, uint8_t length, uint16_t count) {
  uint8_t payload = (uint8_t)((length + 2) / 3);  // three characters per record
  // Widened deliberately. As a uint8_t this wrapped: a header at record 200
  // claiming 200 characters computed 268, which truncated to 12 -- past the
  // check below, and then *backwards*, so the macro replayed the same stretch
  // for ever. The runaway deadline could not stop it either, because every
  // character extends the deadline by exactly the pause it then waits out.
  uint16_t next = (uint16_t)header + 1 + payload;
  // A run whose characters were cut off by the end of the slot. Typing what is
  // there would spray whatever the neighbouring records happen to hold.
  if (next > count) return count;

  for (uint8_t record = 0; record < payload; record++) {
    MacroStep packed = profile_->macroRecord(base, header + 1 + record);
    uint8_t bytes[3] = {packed.type, packed.a, packed.b};
    for (uint8_t offset = 0; offset < 3; offset++) {
      uint8_t index = (uint8_t)(record * 3 + offset);
      if (index >= length) break;
      mkTypeChar(bytes[offset]);
      macroWait(profile_->textDelayMs());
      // macroWait returns early once the loop is stopped, so without this the
      // rest of the run would be typed at full speed with no pause at all.
      if (macroAborted_) return next;
    }
  }
  return next;
}

void KeyEngine::pumpUntil(uint32_t at) {
  while ((int32_t)(millis() - at) < 0) {
    macroPump();
    if (macroAborted_) return;
  }
}

// A recorded move is a *slice* of motion, not a jump. The host sums 50 ms of
// raw mouse counts into one record, so replaying it as a single HID report
// hands the desktop one enormous delta where the mouse had sent fifty small
// ones -- and pointer acceleration reads that as a very fast movement and
// multiplies it. The macro then overshoots, by more the faster it was drawn.
//
// The recording already says how long the motion took: it is the pause that
// follows. Spending that pause moving, a report at a time, replays the gesture
// at the speed it was made, so whatever acceleration curve is in force applies
// to the replay exactly as it applied to the hand. It cancels instead of
// compounding, and nobody has to change a desktop setting for it.
void KeyEngine::emitMove(int8_t dx, int8_t dy, uint16_t overMs) {
  int16_t reachX = dx < 0 ? -dx : dx;
  int16_t reachY = dy < 0 ? -dy : dy;
  int16_t span = reachX > reachY ? reachX : reachY;
  if (span == 0) {
    if (overMs != 0) pumpUntil(millis() + overMs);
    return;
  }

  // One report per millisecond at the most -- the USB frame is the floor --
  // and never less than one count per report, which is what caps this at the
  // distance itself. With no pause to spend, this is a single report: the old
  // behaviour, for a macro that has no timing to honour.
  uint16_t steps = overMs < (uint16_t)span ? overMs : (uint16_t)span;
  if (steps == 0) steps = 1;

  // The host polls the HID endpoint every MK_HID_POLL_INTERVAL_MS, so that is
  // how many reports actually fit in the time available. Asking for more does
  // not send them sooner: each one waits for the endpoint, and the gesture
  // stretches by exactly the ratio between the two intervals. A board polled
  // every 10 ms replayed a 50 ms slice over half a second that way -- the
  // pointer still landed in the right place, just far too slowly, so nothing
  // failed anywhere. Fewer, larger reports keep the duration honest.
  //
  // Paced at MK_MACRO_MOVE_PACE_NUM/DEN of that interval rather than at the
  // interval itself, because the floor is not a budget. At exactly one report
  // per frame the deadline is missed by any delay at all, and a stretched
  // gesture lands short wherever pointer acceleration is on -- Config.h has
  // the measurements.
  uint32_t frames = (uint32_t)MK_HID_POLL_INTERVAL_MS * MK_MACRO_MOVE_PACE_NUM;
  uint16_t carried = (uint16_t)(((uint32_t)overMs * MK_MACRO_MOVE_PACE_DEN) / frames);
  if (carried == 0) carried = 1;
  if (steps > carried) steps = carried;

  uint32_t startedAt = millis();
  int16_t doneX = 0;
  int16_t doneY = 0;
  for (uint16_t step = 1; step <= steps; step++) {
    int16_t x = (int16_t)((int32_t)dx * step / steps) - doneX;
    int16_t y = (int16_t)((int32_t)dy * step / steps) - doneY;
    doneX += x;
    doneY += y;
    if (x != 0 || y != 0) mkMouseMove((int8_t)x, (int8_t)y);
    // An absolute target, so the millisecond a report itself costs comes out
    // of the interval. Waiting a fixed amount after each one would stretch the
    // gesture to twice the time it was recorded over.
    if (overMs != 0) {
      pumpUntil(startedAt + (uint32_t)overMs * step / steps);
    }
  }
}

uint16_t KeyEngine::runMoves(uint16_t base, uint16_t first, uint16_t count) {
  // Consecutive move records are one slice that was too long for a signed
  // byte, so they share the pause that follows them rather than each getting
  // it. Splitting a slice must not stretch the gesture.
  uint16_t end = first;
  while (end < count && profile_->macroRecord(base, end).type == ACT_MOUSE_MOVE) end++;

  uint16_t next = end;
  uint16_t pauseMs = 0;
  if (next < count) {
    MacroStep following = profile_->macroRecord(base, next);
    if (following.type == ACT_DELAY) {
      pauseMs = (uint16_t)following.a * 10;
      next++;
    }
  }

  // The pause is consumed here, so the deadline has to grow by it exactly as
  // it would have had ACT_DELAY reached macroWait.
  if (macroDeadline_ != 0) macroDeadline_ += pauseMs;

  // No pause after it means the recording ended here, not that the pointer
  // teleported: it is still one slice of motion and gets a slice's worth.
  uint16_t spread = pauseMs == 0 ? (uint16_t)MK_MACRO_MOVE_SLICE_MS
                    : pauseMs > MK_MACRO_MOVE_SPREAD_MAX_MS
                        ? (uint16_t)MK_MACRO_MOVE_SPREAD_MAX_MS
                        : pauseMs;
  if (pauseMs == 0 && macroDeadline_ != 0) macroDeadline_ += spread;
  uint16_t moves = end - first;
  uint16_t perMove = moves != 0 ? (uint16_t)(spread / moves) : 0;

  for (uint16_t at = first; at < end; at++) {
    MacroStep move = profile_->macroRecord(base, at);
    emitMove((int8_t)move.a, (int8_t)move.b, perMove);
    // Same reason as the text run: emitMove's pacing comes back early once the
    // loop is stopped, and the remaining slices would then arrive as a jump.
    if (macroAborted_) return next;
  }

  // Whatever of the pause was not spent moving is still a pause.
  if (pauseMs > spread) pumpUntil(millis() + (uint16_t)(pauseMs - spread));
  return next;
}

void KeyEngine::runMacro(uint8_t slot, uint8_t key, uint32_t now, uint8_t loops) {
  // No clamp against MK_MACRO_MAX_RECORDS: the stored count cannot exceed what
  // its own width holds, and boards/board.h refuses a board whose ceiling does
  // not fit that width. Reading past the region is what actually needs
  // guarding, and macroRecord does that per record.
  uint16_t count = profile_->macroRecordCount(slot);
  uint16_t base = profile_->macroBase(slot);

  // 0 and 1 are the same instruction. Zero is what a profile written before
  // loop counts existed has in that byte, so this is also the migration.
  if (loops == 0) loops = 1;

  // Only a loop can be stopped early, and only presses that arrive after it
  // started count as a stop. See the members' comment in the header.
  macroAbortArmed_ = loops > 1;
  macroStartMask_ = input_->pressedMask();
  macroAborted_ = false;

  leds_->noteMacroBusy(key, now);

  for (uint8_t pass = 0; pass < loops; pass++) {
    // Renewed per pass, not per run. MK_MACRO_MAX_RUN_MS budgets HID *work* --
    // authored pauses extend it via macroWait -- and one budget shared across
    // 255 passes would stop the loop partway, silently, and further in the
    // longer the macro. A pass still gets the same ceiling it always had, so a
    // corrupt slot that never yields is caught in the first one.
    macroDeadline_ = millis() + MK_MACRO_MAX_RUN_MS;

    uint16_t index = 0;
    while (index < count) {
      // Every record, not only the pauses: a macro with no delay in it -- a drag
      // is exactly that -- would otherwise never yield at all.
      macroPump();
      if (macroAborted_) break;

      // A macro runs inline, so both a record count and a wall-clock ceiling
      // guard against a corrupt slot locking the firmware out of its scan loop.
      if ((int32_t)(millis() - macroDeadline_) >= 0) break;

      MacroStep record = profile_->macroRecord(base, index);

      if (record.type == ACT_TEXT) {
        index = runText(base, index, record.a, count);
        continue;
      }
      if (record.type == ACT_MOUSE_MOVE) {
        index = runMoves(base, index, count);
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

    // Between passes as well as at the end, so each pass starts from nothing
    // held. A recording is allowed to end mid-hold -- "press W" with no release
    // is half a hold, and the normaliser keeps it -- and looping that would
    // press an already-pressed key, then leave it down when the loop stopped.
    mkMouseReleaseAll();
    mkKeyboardReleaseAll();

    if (macroAborted_) break;
  }

  macroDeadline_ = 0;
  macroAbortArmed_ = false;

  leds_->noteMacroDone(key, millis());
}

void KeyEngine::handleEvent(const KeyEvent &event, uint32_t now) {
  if (event.released) {
    // End of a hold. Nothing is bound to one, so this is a report and no more.
    if (onKey_ != NULL) onKey_(event.key, event.gesture, true);
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
    if (onKey_ != NULL) onKey_(event.key, event.gesture, false);
    return;
  }
  leds_->notePress(event.key, now);

  Action action = profile_->action(event.key, event.gesture);

  if (action.type == ACT_NONE) {
    leds_->noteUnbound(event.key, now);
  }

  dispatch(action, event.key, now);
  if (onKey_ != NULL) onKey_(event.key, event.gesture, false);
}

void KeyEngine::update(uint32_t now) {
  // Asked for before anything else this pass, so the request is reported even
  // if the same tick also produces ordinary key events.
  int8_t recordKey = input_->takeRecordRequest();
  if (recordKey >= 0 && onRecord_ != NULL) {
    // The key is still down. Suppressing it stops the release from firing the
    // binding as well: the person is programming the key, not using it.
    input_->suppressUntilRelease((mk_keymask_t)((mk_keymask_t)1 << recordKey));
    onRecord_((uint8_t)recordKey, input_->recordGesture());
  }

  if (!hidEnabled_ && now >= MK_BOOT_GRACE_MS) hidEnabled_ = true;

  KeyEvent event;
  while (input_->nextEvent(&event)) {
    handleEvent(event, now);
  }

  if (!doubleTapMaskReady_) refreshDoubleTapMask();
}
