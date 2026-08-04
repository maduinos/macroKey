#include "Profile.h"

#include <EEPROM.h>
#include <stdlib.h>

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
  H_RESERVED = 11,
  H_CRC_LO = 12,
  H_CRC_HI = 13
};

void writeAction(uint16_t address, const Action &action) {
  EEPROM.update(address + 0, action.type);
  EEPROM.update(address + 1, action.a);
  EEPROM.update(address + 2, action.b);
  EEPROM.update(address + 3, action.c);
}

Action makeKey(uint8_t modifiers, uint8_t keycode, uint8_t flags = KEYF_NONE) {
  Action action = {ACT_KEY, modifiers, keycode, flags};
  return action;
}

}  // namespace

uint16_t Profile::keymapAddress(uint8_t layer, uint8_t key, uint8_t gesture) const {
  uint16_t index = ((uint16_t)layer * MK_KEY_COUNT + key) * MK_GESTURE_COUNT + gesture;
  return MK_KEYMAP_OFFSET + index * sizeof(Action);
}

bool Profile::begin() {
  bool valid = EEPROM.read(MK_HEADER_OFFSET + H_MAGIC + 0) == MK_PROFILE_MAGIC0 &&
               EEPROM.read(MK_HEADER_OFFSET + H_MAGIC + 1) == MK_PROFILE_MAGIC1 &&
               EEPROM.read(MK_HEADER_OFFSET + H_MAGIC + 2) == MK_PROFILE_MAGIC2 &&
               EEPROM.read(MK_HEADER_OFFSET + H_MAGIC + 3) == MK_PROFILE_MAGIC3 &&
               EEPROM.read(MK_HEADER_OFFSET + H_SCHEMA) == MK_PROFILE_SCHEMA &&
               EEPROM.read(MK_HEADER_OFFSET + H_LAYERS) == MK_LAYER_COUNT &&
               EEPROM.read(MK_HEADER_OFFSET + H_KEYS) == MK_KEY_COUNT &&
               EEPROM.read(MK_HEADER_OFFSET + H_GESTURES) == MK_GESTURE_COUNT;

  if (valid) {
    uint16_t stored = (uint16_t)EEPROM.read(MK_HEADER_OFFSET + H_CRC_LO) |
                      ((uint16_t)EEPROM.read(MK_HEADER_OFFSET + H_CRC_HI) << 8);
    valid = stored == bodyCrc();
  }

  if (!valid) {
    writeDefaults();
    return false;
  }

  brightness_ = EEPROM.read(MK_HEADER_OFFSET + H_BRIGHTNESS);
  baseLayer_ = EEPROM.read(MK_HEADER_OFFSET + H_BASE_LAYER);
  flags_ = EEPROM.read(MK_HEADER_OFFSET + H_FLAGS);
  if (baseLayer_ >= MK_LAYER_COUNT) baseLayer_ = 0;
  return true;
}

Action Profile::action(uint8_t layer, uint8_t key, uint8_t gesture) const {
  Action result = {ACT_NONE, 0, 0, 0};
  if (layer >= MK_LAYER_COUNT || key >= MK_KEY_COUNT || gesture >= MK_GESTURE_COUNT) {
    return result;
  }
  uint16_t address = keymapAddress(layer, key, gesture);
  result.type = EEPROM.read(address + 0);
  result.a = EEPROM.read(address + 1);
  result.b = EEPROM.read(address + 2);
  result.c = EEPROM.read(address + 3);
  if (result.type >= ACT_TYPE_COUNT) result.type = ACT_NONE;
  return result;
}

Rgb Profile::paletteColor(uint8_t layer, uint8_t led) const {
  Rgb color = {0, 0, 0};
  if (layer >= MK_LAYER_COUNT || led >= MK_LED_COUNT) return color;
  uint16_t address = MK_PALETTE_OFFSET + ((uint16_t)layer * MK_LED_COUNT + led) * 3;
  color.r = EEPROM.read(address + 0);
  color.g = EEPROM.read(address + 1);
  color.b = EEPROM.read(address + 2);
  return color;
}

bool Profile::chord(uint8_t slot, uint8_t *mask, Action *action) const {
  if (slot >= MK_CHORD_SLOTS) return false;
  uint16_t address = MK_CHORD_OFFSET + (uint16_t)slot * MK_CHORD_ENTRY_SIZE;
  uint8_t storedMask = EEPROM.read(address);
  uint8_t type = EEPROM.read(address + 1);
  // A chord needs at least two keys to be meaningful, and an action to run.
  if (storedMask == 0 || type == ACT_NONE || type >= ACT_TYPE_COUNT) return false;
  *mask = storedMask;
  action->type = type;
  action->a = EEPROM.read(address + 2);
  action->b = EEPROM.read(address + 3);
  action->c = EEPROM.read(address + 4);
  return true;
}

uint8_t Profile::macroStepCount(uint8_t slot) const {
  if (slot >= MK_MACRO_SLOTS) return 0;
  return EEPROM.read(MK_MACRO_OFFSET + (uint16_t)slot * 2 + 1);
}

MacroStep Profile::macroStep(uint8_t slot, uint8_t index) const {
  MacroStep step = {ACT_NONE, 0, 0};
  if (slot >= MK_MACRO_SLOTS || index >= macroStepCount(slot)) return step;

  uint8_t stepOffset = EEPROM.read(MK_MACRO_OFFSET + (uint16_t)slot * 2);
  uint16_t address = MK_MACRO_OFFSET + MK_MACRO_INDEX_SIZE +
                     ((uint16_t)stepOffset + index) * MK_MACRO_STEP_SIZE;
  if (address + MK_MACRO_STEP_SIZE > MK_PROFILE_SIZE) return step;

  step.type = EEPROM.read(address + 0);
  step.a = EEPROM.read(address + 1);
  step.b = EEPROM.read(address + 2);
  if (step.type >= ACT_TYPE_COUNT) step.type = ACT_NONE;
  return step;
}

void Profile::readRaw(uint16_t offset, uint8_t *out, uint16_t length) const {
  for (uint16_t i = 0; i < length; i++) {
    uint16_t address = offset + i;
    out[i] = address < MK_PROFILE_SIZE ? EEPROM.read(address) : 0;
  }
}

uint16_t Profile::bodyCrc() const {
  uint16_t crc = 0xFFFF;
  for (uint16_t address = MK_HEADER_SIZE; address < MK_PROFILE_SIZE; address++) {
    uint8_t byte = EEPROM.read(address);
    crc = mkCrc16(&byte, 1, crc);
  }
  return crc;
}

void Profile::writeHeaderFields(uint16_t crc) {
  EEPROM.update(MK_HEADER_OFFSET + H_MAGIC + 0, MK_PROFILE_MAGIC0);
  EEPROM.update(MK_HEADER_OFFSET + H_MAGIC + 1, MK_PROFILE_MAGIC1);
  EEPROM.update(MK_HEADER_OFFSET + H_MAGIC + 2, MK_PROFILE_MAGIC2);
  EEPROM.update(MK_HEADER_OFFSET + H_MAGIC + 3, MK_PROFILE_MAGIC3);
  EEPROM.update(MK_HEADER_OFFSET + H_SCHEMA, MK_PROFILE_SCHEMA);
  EEPROM.update(MK_HEADER_OFFSET + H_LAYERS, MK_LAYER_COUNT);
  EEPROM.update(MK_HEADER_OFFSET + H_KEYS, MK_KEY_COUNT);
  EEPROM.update(MK_HEADER_OFFSET + H_GESTURES, MK_GESTURE_COUNT);
  EEPROM.update(MK_HEADER_OFFSET + H_BRIGHTNESS, brightness_);
  EEPROM.update(MK_HEADER_OFFSET + H_BASE_LAYER, baseLayer_);
  EEPROM.update(MK_HEADER_OFFSET + H_FLAGS, flags_);
  EEPROM.update(MK_HEADER_OFFSET + H_RESERVED, 0);
  EEPROM.update(MK_HEADER_OFFSET + H_CRC_LO, (uint8_t)(crc & 0xFF));
  EEPROM.update(MK_HEADER_OFFSET + H_CRC_HI, (uint8_t)(crc >> 8));
  EEPROM.update(MK_HEADER_OFFSET + 14, 0);
  EEPROM.update(MK_HEADER_OFFSET + 15, 0);
}

void Profile::saveHeader() { writeHeaderFields(bodyCrc()); }

void Profile::writeDefaults() {
  for (uint16_t address = MK_HEADER_SIZE; address < MK_PROFILE_SIZE; address++) {
    EEPROM.update(address, 0);
  }

  // Layer 0 taps: hyper (ctrl+alt+shift) plus 1..8. Nothing sane binds that
  // combination, so the pad is useful the moment it is plugged in and it never
  // fights an application shortcut.
  const uint8_t hyper = MOD_CTRL | MOD_ALT | MOD_SHIFT;
  for (uint8_t key = 0; key < MK_KEY_COUNT; key++) {
    writeAction(keymapAddress(0, key, GESTURE_TAP), makeKey(hyper, '1' + key));
  }

  // Key 8 held is the layer key, and its LED colour makes the shift visible.
  Action toLayer1 = {ACT_LAYER_MOMENTARY, 1, 0, 0};
  writeAction(keymapAddress(0, MK_KEY_COUNT - 1, GESTURE_HOLD), toLayer1);

  // Layer 1 is a media / desktop-app strip: four consumer controls that need no
  // host app, then host tokens for whatever the desktop app wants to offer.
  const uint16_t consumerUsages[4] = {0x00EA, 0x00E9, 0x00E2, 0x00CD};
  for (uint8_t key = 0; key < 4; key++) {
    Action action = {ACT_CONSUMER, (uint8_t)(consumerUsages[key] & 0xFF),
                     (uint8_t)(consumerUsages[key] >> 8), 0};
    writeAction(keymapAddress(1, key, GESTURE_TAP), action);
  }
  for (uint8_t key = 4; key < MK_KEY_COUNT - 1; key++) {
    Action action = {ACT_HOST, (uint8_t)(key - 4), 0, 0};
    writeAction(keymapAddress(1, key, GESTURE_TAP), action);
  }

  // Chord: keys 1+2 together ask the host to stop whatever it is running.
  uint16_t chord0 = MK_CHORD_OFFSET;
  EEPROM.update(chord0, 0x03);
  Action panic = {ACT_HOST, 200, 0, 0};
  writeAction(chord0 + 1, panic);

  // One resting colour per layer so the active layer is readable at a glance.
  const Rgb layerColors[4] = {{0, 16, 20}, {28, 12, 0}, {18, 0, 28}, {0, 24, 6}};
  for (uint8_t layer = 0; layer < MK_LAYER_COUNT; layer++) {
    const Rgb &color = layerColors[layer < 4 ? layer : 0];
    for (uint8_t led = 0; led < MK_LED_COUNT; led++) {
      uint16_t address = MK_PALETTE_OFFSET + ((uint16_t)layer * MK_LED_COUNT + led) * 3;
      EEPROM.update(address + 0, color.r);
      EEPROM.update(address + 1, color.g);
      EEPROM.update(address + 2, color.b);
    }
  }

  brightness_ = MK_LED_DEFAULT_BRIGHTNESS;
  baseLayer_ = 0;
  flags_ = 0;
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

bool Profile::stageChunk(uint8_t sequence, const uint8_t *data, uint8_t length) {
  if (stage_ == NULL) return false;
  uint16_t offset = (uint16_t)sequence * MK_PROFILE_CHUNK_BYTES;
  if (offset + length > stageBytes_) return false;
  memcpy(stage_ + offset, data, length);
  stageReceived_ += length;
  return true;
}

bool Profile::stageCommit() {
  if (stage_ == NULL) return false;
  bool ok = stageReceived_ >= stageBytes_ &&
            mkCrc16(stage_ + MK_HEADER_SIZE, stageBytes_ - MK_HEADER_SIZE) == stageCrc_;
  if (ok) {
    for (uint16_t address = MK_HEADER_SIZE; address < stageBytes_; address++) {
      EEPROM.update(address, stage_[address]);
    }
    // The staged header carries the tunables; magic and CRC are ours to write.
    brightness_ = stage_[MK_HEADER_OFFSET + H_BRIGHTNESS];
    baseLayer_ = stage_[MK_HEADER_OFFSET + H_BASE_LAYER];
    if (baseLayer_ >= MK_LAYER_COUNT) baseLayer_ = 0;
    flags_ = stage_[MK_HEADER_OFFSET + H_FLAGS];
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
