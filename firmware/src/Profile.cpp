#include "Profile.h"

#include <stdlib.h>

#include "Storage.h"
#include "Util.h"

namespace {

// Header field offsets, relative to MK_HEADER_OFFSET.
enum : uint16_t {
  H_MAGIC = 0,      // 4 bytes
  H_SCHEMA = 4,
  H_LAYERS = 5,
  H_KEYS = 6,
  H_GESTURES = 7,
  H_BRIGHTNESS = 8,
  H_BASE_LAYER = 9,
  H_FLAGS = 10,
  H_TEXT_DELAY = 11,  // was reserved; 0 means the build-time default
  H_CRC_LO = 12,
  H_CRC_HI = 13
};

void writeAction(uint16_t address, const Action &action) {
  mkStoreUpdate(address + 0, action.type);
  mkStoreUpdate(address + 1, action.a);
  mkStoreUpdate(address + 2, action.b);
  mkStoreUpdate(address + 3, action.c);
}

Action makeKey(uint8_t modifiers, uint8_t keycode, uint8_t flags = KEYF_NONE) {
  Action action = {ACT_KEY, modifiers, keycode, flags};
  return action;
}

}  // namespace

uint16_t Profile::keymapAddress(uint8_t key, uint8_t gesture) const {
  return MK_KEYMAP_OFFSET + ((uint16_t)key * MK_KEYMAP_GESTURES + gesture) * sizeof(Action);
}

bool Profile::begin() {
  // Before the first read, not once at the first write: on a flash-emulated
  // store there is nothing to read until this has run.
  mkStoreBegin();

  bool valid = mkStoreRead(MK_HEADER_OFFSET + H_MAGIC + 0) == MK_PROFILE_MAGIC0 &&
               mkStoreRead(MK_HEADER_OFFSET + H_MAGIC + 1) == MK_PROFILE_MAGIC1 &&
               mkStoreRead(MK_HEADER_OFFSET + H_MAGIC + 2) == MK_PROFILE_MAGIC2 &&
               mkStoreRead(MK_HEADER_OFFSET + H_MAGIC + 3) == MK_PROFILE_MAGIC3 &&
               mkStoreRead(MK_HEADER_OFFSET + H_SCHEMA) == MK_PROFILE_SCHEMA &&
               mkStoreRead(MK_HEADER_OFFSET + H_KEYS) == MK_KEY_COUNT &&
               mkStoreRead(MK_HEADER_OFFSET + H_GESTURES) == MK_KEYMAP_GESTURES;

  if (valid) {
    uint16_t stored = (uint16_t)mkStoreRead(MK_HEADER_OFFSET + H_CRC_LO) |
                      ((uint16_t)mkStoreRead(MK_HEADER_OFFSET + H_CRC_HI) << 8);
    valid = stored == bodyCrc();
  }

  if (!valid) {
    writeDefaults();
    return false;
  }

  brightness_ = mkStoreRead(MK_HEADER_OFFSET + H_BRIGHTNESS);
  flags_ = mkStoreRead(MK_HEADER_OFFSET + H_FLAGS);
  textDelayMs_ = mkStoreRead(MK_HEADER_OFFSET + H_TEXT_DELAY);
  return true;
}

Action Profile::action(uint8_t key, uint8_t gesture) const {
  Action result = {ACT_NONE, 0, 0, 0};
  // GESTURE_HOLD is past the end of the keymap on purpose: it is how recording
  // starts, so it is reported but never bound, and asking for it reads as the
  // empty action rather than off the end of the region.
  if (key >= MK_KEY_COUNT || gesture >= MK_KEYMAP_GESTURES) return result;
  uint16_t address = keymapAddress(key, gesture);
  result.type = mkStoreRead(address + 0);
  result.a = mkStoreRead(address + 1);
  result.b = mkStoreRead(address + 2);
  result.c = mkStoreRead(address + 3);
  if (result.type >= ACT_TYPE_COUNT) result.type = ACT_NONE;
  return result;
}

Rgb Profile::paletteColor(uint8_t led) const {
  Rgb color = {0, 0, 0};
  if (led >= MK_LED_COUNT) return color;
  uint16_t address = MK_PALETTE_OFFSET + (uint16_t)led * 3;
  color.r = mkStoreRead(address + 0);
  color.g = mkStoreRead(address + 1);
  color.b = mkStoreRead(address + 2);
  return color;
}

uint16_t Profile::macroRecordCount(uint8_t slot) const {
  if (slot >= MK_MACRO_SLOTS) return 0;
  uint16_t address = MK_MACRO_OFFSET + (uint16_t)slot * MK_MACRO_COUNT_BYTES;
  uint16_t count = mkStoreRead(address);
#if MK_MACRO_COUNT_BYTES == 2
  count |= (uint16_t)mkStoreRead(address + 1) << 8;
#endif
  return count;
}

uint16_t Profile::macroBase(uint8_t slot) const {
  if (slot >= MK_MACRO_SLOTS) return 0;
  uint16_t base = 0;
  for (uint8_t earlier = 0; earlier < slot; earlier++) {
    base += macroRecordCount(earlier);
  }
  return base;
}

MacroStep Profile::macroRecord(uint16_t base, uint16_t index) const {
  MacroStep record = {ACT_NONE, 0, 0};
  uint16_t position = base + index;
  if (position >= MK_MACRO_RECORD_CAPACITY) return record;

  uint16_t address =
      MK_MACRO_OFFSET + MK_MACRO_INDEX_SIZE + position * MK_MACRO_RECORD_SIZE;
  record.type = mkStoreRead(address + 0);
  record.a = mkStoreRead(address + 1);
  record.b = mkStoreRead(address + 2);
  return record;
}

void Profile::readRaw(uint16_t offset, uint8_t *out, uint16_t length) const {
  for (uint16_t i = 0; i < length; i++) {
    uint16_t address = offset + i;
    out[i] = address < MK_PROFILE_SIZE ? mkStoreRead(address) : 0;
  }
}

uint16_t Profile::bodyCrc() const {
  uint16_t crc = 0xFFFF;
  for (uint16_t address = MK_HEADER_SIZE; address < MK_PROFILE_SIZE; address++) {
    uint8_t byte = mkStoreRead(address);
    crc = mkCrc16(&byte, 1, crc);
  }
  return crc;
}

void Profile::writeHeaderFields(uint16_t crc) {
  mkStoreUpdate(MK_HEADER_OFFSET + H_MAGIC + 0, MK_PROFILE_MAGIC0);
  mkStoreUpdate(MK_HEADER_OFFSET + H_MAGIC + 1, MK_PROFILE_MAGIC1);
  mkStoreUpdate(MK_HEADER_OFFSET + H_MAGIC + 2, MK_PROFILE_MAGIC2);
  mkStoreUpdate(MK_HEADER_OFFSET + H_MAGIC + 3, MK_PROFILE_MAGIC3);
  mkStoreUpdate(MK_HEADER_OFFSET + H_SCHEMA, MK_PROFILE_SCHEMA);
  mkStoreUpdate(MK_HEADER_OFFSET + H_LAYERS, 1);      // retired, kept for layout
  mkStoreUpdate(MK_HEADER_OFFSET + H_KEYS, MK_KEY_COUNT);
  mkStoreUpdate(MK_HEADER_OFFSET + H_GESTURES, MK_KEYMAP_GESTURES);
  mkStoreUpdate(MK_HEADER_OFFSET + H_BRIGHTNESS, brightness_);
  mkStoreUpdate(MK_HEADER_OFFSET + H_BASE_LAYER, 0);  // retired, kept for layout
  mkStoreUpdate(MK_HEADER_OFFSET + H_FLAGS, flags_);
  mkStoreUpdate(MK_HEADER_OFFSET + H_TEXT_DELAY, textDelayMs_);
  mkStoreUpdate(MK_HEADER_OFFSET + H_CRC_LO, (uint8_t)(crc & 0xFF));
  mkStoreUpdate(MK_HEADER_OFFSET + H_CRC_HI, (uint8_t)(crc >> 8));
  mkStoreUpdate(MK_HEADER_OFFSET + 14, 0);
  mkStoreUpdate(MK_HEADER_OFFSET + 15, 0);
  // Every write path -- saveHeader, writeDefaults, stageCommit -- finishes
  // here, which makes this the one place the store has to be flushed.
  mkStoreCommit();
}

void Profile::saveHeader() { writeHeaderFields(bodyCrc()); }

void Profile::writeDefaults() {
  for (uint16_t address = MK_HEADER_SIZE; address < MK_PROFILE_SIZE; address++) {
    mkStoreUpdate(address, 0);
  }

  // Layer 0 taps: hyper (ctrl+alt+shift) plus 1..8. Nothing sane binds that
  // combination, so the pad is useful the moment it is plugged in and it never
  // fights an application shortcut.
  const uint8_t hyper = MOD_CTRL | MOD_ALT | MOD_SHIFT;
  for (uint8_t key = 0; key < MK_KEY_COUNT; key++) {
    writeAction(keymapAddress(key, GESTURE_TAP), makeKey(hyper, '1' + key));
  }

  // No layer switching: eight keys that each do one thing, and everything else
  // is recorded onto them by holding the key.

  // A dim blue-grey resting glow. Off reads as unplugged, and this is what the
  // pixel shows most of the time; the recording and result colours are driven
  // by the host and compose above it.
  const Rgb resting = {60, 80, 115};
  for (uint8_t led = 0; led < MK_LED_COUNT; led++) {
    uint16_t address = MK_PALETTE_OFFSET + led * 3;
    mkStoreUpdate(address + 0, resting.r);
    mkStoreUpdate(address + 1, resting.g);
    mkStoreUpdate(address + 2, resting.b);
  }

  brightness_ = MK_LED_DEFAULT_BRIGHTNESS;
  flags_ = 0;
  textDelayMs_ = 0;
  writeHeaderFields(bodyCrc());
}

// ----------------------------------------------------------- staged writes --

bool Profile::stageBegin(uint16_t byteCount, uint16_t crc) {
  stageAbort();
  if (byteCount != MK_PROFILE_SIZE) return false;
  stage_ = (uint8_t *)malloc(byteCount);
  if (stage_ == NULL) return false;
  memset(stage_, 0, byteCount);
  stageBytes_ = byteCount;
  stageCrc_ = crc;
  stageReceived_ = 0;
  stageStartedAt_ = millis();
  return true;
}

bool Profile::stageChunk(uint16_t sequence, const uint8_t *data, uint8_t length) {
  if (stage_ == NULL) return false;
  uint32_t wideOffset = (uint32_t)sequence * MK_PROFILE_CHUNK_BYTES;
  if (wideOffset > 65535) return false;
  uint16_t offset = (uint16_t)wideOffset;
  if (offset + length > stageBytes_) return false;
  memcpy(stage_ + offset, data, length);
  stageReceived_ += length;
  stageStartedAt_ = millis();
  return true;
}

bool Profile::stageCommit() {
  if (stage_ == NULL) return false;
  bool ok = stageReceived_ >= stageBytes_ &&
            mkCrc16(stage_ + MK_HEADER_SIZE, stageBytes_ - MK_HEADER_SIZE) == stageCrc_;
  if (ok) {
    for (uint16_t address = MK_HEADER_SIZE; address < stageBytes_; address++) {
      mkStoreUpdate(address, stage_[address]);
    }
    // The staged header carries the tunables; magic and CRC are ours to write.
    brightness_ = stage_[MK_HEADER_OFFSET + H_BRIGHTNESS];
    flags_ = stage_[MK_HEADER_OFFSET + H_FLAGS];
    textDelayMs_ = stage_[MK_HEADER_OFFSET + H_TEXT_DELAY];
    writeHeaderFields(stageCrc_);
  }
  stageAbort();
  return ok;
}

void Profile::stageAbort() {
  if (stage_ != NULL) {
    free(stage_);
    stage_ = NULL;
  }
  stageBytes_ = 0;
  stageReceived_ = 0;
}

void Profile::stageTick(uint32_t now) {
  if (stage_ == NULL) return;
  if (now - stageStartedAt_ > MK_PROFILE_STAGE_TIMEOUT_MS) stageAbort();
}
