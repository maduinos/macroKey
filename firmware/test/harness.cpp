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
//   replay <slot> [loops] [stop-at-ms]
//                     run a macro through the real KeyEngine, print HID calls.
//                     `stop-at-ms` presses a second key at that point on the
//                     clock, which is how a looping macro is stopped.
//   serial            feed lines to the real SerialProtocol, print what changed
//   macro-serial <slot> <line>
//                     send a line to the pad from *inside* a replaying macro,
//                     which is where a long loop leaves the link
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
#include "SerialProtocol.h"
#undef private

uint32_t gClock = 0;
uint32_t gClockStep = 1;
bool gPinLow[32];
SerialStub Serial;
EEPROMStub EEPROM;
KeyboardStub Keyboard;
MouseStub Mouse;
uint8_t DDRB, PORTB, DDRD, PORTD;
uint32_t gPressPinAt = 0;
uint8_t gPressPin = 0;

static Profile gProfile;
static ButtonInput gInput;
static LedController gLeds;
static KeyEngine gEngine;
static SerialProtocol gSerial;

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
  printf("move_slice_ms %u\n", MK_MACRO_MOVE_SLICE_MS);
  printf("hid_poll_interval_ms %u\n", MK_HID_POLL_INTERVAL_MS);
  printf("macro_max_loops %u\n", MK_MACRO_MAX_LOOPS);
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
  uint8_t loops = argc > 3 ? (uint8_t)atoi(argv[3]) : 0;
  uint32_t stopAt = argc > 4 ? (uint32_t)strtoul(argv[4], NULL, 10) : 0;

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
  //
  // It is also the key the macro is running *for*, which is why it does not
  // stop a loop: runMacro takes the mask at entry and only counts what arrives
  // after. Without that, every looped macro would stop in its first pass here.
  gPinLow[MK_KEY_PINS[0]] = true;
  // Debounced before the macro starts, because that is the state the pad is
  // really in: a key held across a macro has been held for scans beforehand.
  // runMacro reads the mask at entry, and an undebounced pin is not in it yet,
  // so without this the key arrives mid-macro looking like a fresh press --
  // which is precisely what stops a loop.
  for (uint8_t settle = 0; settle < 8; settle++) {
    gClock += MK_DEBOUNCE_MS;
    gInput.update(gClock);
  }

  // A different key, pressed from inside the macro by the pin stub.
  if (stopAt != 0) {
    gPressPinAt = gClock + stopAt;
    gPressPin = MK_KEY_PINS[1];
  }

  Action run = {ACT_SEQUENCE, slot, loops, 0};
  gEngine.dispatch(run, 0, gClock);
  printf("scanned-during-replay %d\n", gInput.pressedMask() != 0 ? 1 : 0);
  // Whether the key that stopped the loop was swallowed. It must not also fire
  // whatever it is bound to: stopping a macro is not using the key.
  printf("stop-key-suppressed %d\n",
         stopAt == 0 ? -1 : (gInput.keys_[1].suppressed ? 1 : 0));
  return 0;
}

// ------------------------------------------------------------------ serial --

// Brings the whole device up the way firmware.ino does, so a command runs
// against the same objects it would on the pad.
static void bootDevice() {
  gInput.begin();
  gProfile.begin();
  gLeds.begin(&gProfile);
  gEngine.begin(&gProfile, &gInput, &gLeds);
  gSerial.begin(&gProfile, &gEngine, &gLeds);
  gClock = MK_BOOT_GRACE_MS + 1;
  gEngine.update(gClock);  // builds the double-tap mask, as the first loop does
}

// One pass of loop(), for the parts a command can disturb.
static void pump() {
  gSerial.update(gClock);
  gEngine.update(gClock);
  gLeds.update(gClock);
}

static void reportState(const char *when) {
  printf("mask_%s %u\n", when, gInput.doubleTapMask_);
  printf("bright_%s %u\n", when, gLeds.brightness());
}

static int modeSerial(int argc, char **argv) {
  if (!readBlob()) return 2;
  gClockStep = 0;  // this mode drives time itself
  bootDevice();
  reportState("before");

  Serial.out_length = 0;
  Serial.out[0] = '\0';
  if (argc > 2) {
    Serial.feed(argv[2]);
    pump();
  }
  reportState("after");

  // The transcript last: it is many lines, so anything parsed by position
  // stays above it.
  printf("--- transcript\n%s", Serial.out);
  return 0;
}

// ------------------------------------------------------- serial in a macro --

// Sent once, from the first yield inside the macro. That is the window the
// firmware opens by pumping serial from macroYield: a loop can run for most of
// an hour, and the app polls the link every second, so a pad that answered
// nothing would read as unplugged for the whole loop.
static const char *gLineFromInsideMacro = NULL;

static void macroYieldWithSerial() {
  if (gLineFromInsideMacro != NULL) {
    Serial.feed(gLineFromInsideMacro);
    gLineFromInsideMacro = NULL;
  }
  gSerial.update(gClock);
}

static int modeMacroSerial(int argc, char **argv) {
  if (!readBlob()) return 2;
  uint8_t slot = argc > 2 ? (uint8_t)atoi(argv[2]) : 0;

  bootDevice();
  gEngine.setMacroYield(macroYieldWithSerial);
  if (argc > 3) gLineFromInsideMacro = argv[3];

  Serial.out_length = 0;
  Serial.out[0] = '\0';

  Action run = {ACT_SEQUENCE, slot, 1, 0};
  gEngine.dispatch(run, 0, gClock);

  // After the macro, so the contrast is visible: the same line that was refused
  // from inside is accepted once runMacro is off the stack.
  printf("macro_running_after %d\n", gEngine.macroRunning() ? 1 : 0);
  printf("--- transcript\n%s", Serial.out);
  return 0;
}

int main(int argc, char **argv) {
  const char *mode = argc > 1 ? argv[1] : "layout";
  if (strcmp(mode, "layout") == 0) return modeLayout();
  if (strcmp(mode, "profile") == 0) return modeProfile();
  if (strcmp(mode, "buttons") == 0) return modeButtons();
  if (strcmp(mode, "replay") == 0) return modeReplay(argc, argv);
  if (strcmp(mode, "serial") == 0) return modeSerial(argc, argv);
  if (strcmp(mode, "macro-serial") == 0) return modeMacroSerial(argc, argv);
  fprintf(stderr, "unknown mode %s\n", mode);
  return 2;
}
