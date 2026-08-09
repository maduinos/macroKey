// Runs the real firmware sources on a PC, against the stub headers next door.
//
// The point is the two copies of the EEPROM layout: `firmware/src/Profile.h`
// and `macrokey/config/binary.py` describe the same 1024 bytes, and nothing but
// care kept them saying the same thing. A disagreement does not fail anywhere --
// it produces a macro that replays garbage. So the host encoder's bytes are fed
// to the firmware's own reader here, and `tests/test_firmware_agreement.py`
// checks that what comes back is what went in.
//
// Modes, chosen by argv[1]:
//   layout            the layout constants, as the firmware computes them
//   profile           read a blob on stdin, print the keymap and macro records
//   buttons           press patterns -> which slot a record request names
//   replay <slot>     run a macro through the real KeyEngine, print HID calls
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <Arduino.h>
#include <Keyboard.h>
#include <Mouse.h>

// dispatch() and runMacro() are private, and they are exactly what needs
// driving. Reaching them this way keeps the test-only entry point out of the
// firmware, where it would be one more thing to keep working.
#define private public
#include "ButtonInput.h"
#include "Config.h"
#include "KeyEngine.h"
#include "LedController.h"
#include "Profile.h"
#undef private

uint32_t gClock = 0;
uint32_t gClockStep = 1;
bool gPinLow[32];
SerialStub Serial;
EEPROMStub EEPROM;
KeyboardStub Keyboard;
MouseStub Mouse;
uint8_t DDRB, PORTB, DDRD, PORTD;

static Profile gProfile;
static ButtonInput gInput;
static LedController gLeds;
static KeyEngine gEngine;

static bool readBlob() {
  return fread(EEPROM.data, 1, sizeof(EEPROM.data), stdin) == sizeof(EEPROM.data);
}

// ------------------------------------------------------------------ layout --

static int modeLayout() {
  printf("keymap_offset %u\n", MK_KEYMAP_OFFSET);
  printf("keymap_size %u\n", MK_KEYMAP_SIZE);
  printf("keymap_gestures %u\n", MK_KEYMAP_GESTURES);
  printf("palette_offset %u\n", MK_PALETTE_OFFSET);
  printf("macro_offset %u\n", MK_MACRO_OFFSET);
  printf("macro_index_size %u\n", MK_MACRO_INDEX_SIZE);
  printf("macro_record_size %u\n", MK_MACRO_RECORD_SIZE);
  printf("macro_record_capacity %u\n", MK_MACRO_RECORD_CAPACITY);
  printf("macro_max_records %u\n", MK_MACRO_MAX_RECORDS);
  printf("macro_slots %u\n", MK_MACRO_SLOTS);
  printf("profile_size %u\n", MK_PROFILE_SIZE);
  printf("schema %u\n", MK_PROFILE_SCHEMA);
  printf("text_delay_default %u\n", MK_MACRO_TEXT_DELAY_MS);
  return 0;
}

// ----------------------------------------------------------------- profile --

static int modeProfile() {
  if (!readBlob()) return 2;
  printf("valid %d\n", gProfile.begin() ? 1 : 0);
  printf("text_delay %u\n", gProfile.textDelayMs());

  for (uint8_t key = 0; key < MK_KEY_COUNT; key++) {
    for (uint8_t gesture = 0; gesture < MK_GESTURE_COUNT; gesture++) {
      Action action = gProfile.action(key, gesture);
      printf("key %u %s %u %u %u\n", key, gestureName(gesture), action.type, action.a, action.b);
    }
  }

  for (uint8_t slot = 0; slot < MK_MACRO_SLOTS; slot++) {
    uint8_t count = gProfile.macroRecordCount(slot);
    if (count == 0) continue;
    uint16_t base = gProfile.macroBase(slot);
    printf("macro %u", slot);
    for (uint8_t i = 0; i < count; i++) {
      MacroStep record = gProfile.macroRecord(base, i);
      printf(" %u,%u,%u", record.type, record.a, record.b);
    }
    printf("\n");
  }
  return 0;
}

// ----------------------------------------------------------------- buttons --

static const uint8_t TEST_KEY = 0;
static const uint8_t TEST_PIN = MK_KEY_PINS[TEST_KEY];

static void tick(uint32_t ms) {
  for (uint32_t i = 0; i < ms; i += MK_SCAN_INTERVAL_MS) {
    gClock += MK_SCAN_INTERVAL_MS;
    gInput.update(gClock);
  }
}

static void reset() {
  memset(gPinLow, 0, sizeof(gPinLow));
  gClock = 0;
  gInput.begin();
}

static void report(const char *name) {
  int8_t key = gInput.takeRecordRequest();
  printf("%s %s\n", name, key < 0 ? "none" : gestureName(gInput.recordGesture()));
}

static int modeButtons() {
  gClockStep = 0;  // this mode drives time itself
  const uint32_t settle = MK_DEBOUNCE_MS * 3;

  reset();
  tick(400);
  gPinLow[TEST_PIN] = true;
  tick(MK_RECORD_HOLD_MS + settle);
  report("hold");

  reset();
  tick(400);
  gPinLow[TEST_PIN] = true; tick(settle);
  gPinLow[TEST_PIN] = false; tick(settle);
  gPinLow[TEST_PIN] = true; tick(MK_RECORD_HOLD_MS + settle);
  report("tap_then_hold");

  reset();
  tick(400);
  gPinLow[TEST_PIN] = true; tick(settle);
  gPinLow[TEST_PIN] = false; tick(MK_DOUBLE_TAP_MS + 100);
  gPinLow[TEST_PIN] = true; tick(MK_RECORD_HOLD_MS + settle);
  report("tap_pause_hold");

  reset();
  tick(400);
  gPinLow[TEST_PIN] = true; tick(500);
  report("short_hold");

  // The same gesture on a key whose double slot is already full. That arms the
  // tap-deferral mask, which is a different path through onPressEdge -- and it
  // is the ordinary case, because correcting a double macro means doing this.
  reset();
  gInput.setDoubleTapMask(1 << TEST_KEY);
  tick(400);
  gPinLow[TEST_PIN] = true; tick(settle);
  gPinLow[TEST_PIN] = false; tick(settle);
  gPinLow[TEST_PIN] = true; tick(MK_RECORD_HOLD_MS + settle);
  report("tap_then_hold_when_double_is_bound");
  gInput.setDoubleTapMask(0);

  // A first press before anything has ever been released. `releasedAt` starts
  // at zero, which read as "released at uptime 0": for the first double-tap
  // window after boot a plain hold programmed the double slot.
  reset();
  tick(50);
  gPinLow[TEST_PIN] = true; tick(MK_RECORD_HOLD_MS + settle);
  report("hold_just_after_boot");
  return 0;
}

// ------------------------------------------------------------------ replay --

static int modeReplay(int argc, char **argv) {
  if (!readBlob()) return 2;
  uint8_t slot = argc > 2 ? (uint8_t)atoi(argv[2]) : 0;

  gInput.begin();
  gProfile.begin();
  gLeds.begin(&gProfile);
  gEngine.begin(&gProfile, &gInput, &gLeds);
  gClock = MK_BOOT_GRACE_MS + 1;   // past the window where HID is suppressed
  gEngine.update(gClock);

  // Held down for the whole replay. A macro blocks loop(), and the scan has to
  // keep running through it: the first tap of a double-record gesture fires the
  // key's macro, and if the scan stopped there the second press would be
  // timestamped after it finished -- seconds past the pair window, so recording
  // into the double slot was impossible on any key that already had one.
  gPinLow[MK_KEY_PINS[0]] = true;

  Action run = {ACT_SEQUENCE, slot, 0, 0};
  gEngine.dispatch(run, 0, gClock);
  printf("scanned-during-replay %d\n", gInput.pressedMask() != 0 ? 1 : 0);
  return 0;
}

int main(int argc, char **argv) {
  const char *mode = argc > 1 ? argv[1] : "layout";
  if (strcmp(mode, "layout") == 0) return modeLayout();
  if (strcmp(mode, "profile") == 0) return modeProfile();
  if (strcmp(mode, "buttons") == 0) return modeButtons();
  if (strcmp(mode, "replay") == 0) return modeReplay(argc, argv);
  fprintf(stderr, "unknown mode %s\n", mode);
  return 2;
}
