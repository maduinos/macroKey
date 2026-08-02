// EEPROM-backed profile store.
//
// The keymap is *not* mirrored into SRAM. Slots are read straight out of EEPROM
// when a key fires; at four cycles per byte that is instant on a human
// timescale and it buys back 384 bytes of the ATmega32u4's 2.5 KB.
#pragma once

#include <Arduino.h>

#include "ActionTypes.h"
#include "Config.h"

#define MK_PROFILE_MAGIC0 'M'
#define MK_PROFILE_MAGIC1 'K'
#define MK_PROFILE_MAGIC2 'E'
#define MK_PROFILE_MAGIC3 'Y'
#define MK_PROFILE_SCHEMA 1

// Flat EEPROM layout. Every region is a fixed-stride array so lookups are
// address arithmetic with no scanning.
static const uint16_t MK_HEADER_OFFSET = 0;
static const uint16_t MK_HEADER_SIZE = 16;
static const uint16_t MK_KEYMAP_OFFSET = MK_HEADER_OFFSET + MK_HEADER_SIZE;
static const uint16_t MK_KEYMAP_SIZE =
    (uint16_t)MK_LAYER_COUNT * MK_KEY_COUNT * MK_GESTURE_COUNT * sizeof(Action);
static const uint16_t MK_CHORD_OFFSET = MK_KEYMAP_OFFSET + MK_KEYMAP_SIZE;
static const uint16_t MK_CHORD_ENTRY_SIZE = 1 + sizeof(Action);
static const uint16_t MK_CHORD_SIZE = MK_CHORD_SLOTS * MK_CHORD_ENTRY_SIZE;
static const uint16_t MK_PALETTE_OFFSET = MK_CHORD_OFFSET + MK_CHORD_SIZE;
static const uint16_t MK_PALETTE_SIZE = (uint16_t)MK_LAYER_COUNT * MK_LED_COUNT * 3;
static const uint16_t MK_MACRO_OFFSET = MK_PALETTE_OFFSET + MK_PALETTE_SIZE;
static const uint16_t MK_MACRO_INDEX_SIZE = MK_MACRO_SLOTS * 2;  // offset, count
static const uint16_t MK_MACRO_STEP_SIZE = 3;
static const uint16_t MK_MACRO_REGION_SIZE = 480;
static const uint16_t MK_PROFILE_SIZE = MK_MACRO_OFFSET + MK_MACRO_REGION_SIZE;

static_assert(MK_PROFILE_SIZE <= 1024, "profile does not fit in ATmega32u4 EEPROM");

struct Rgb {
  uint8_t r;
  uint8_t g;
  uint8_t b;
};

struct MacroStep {
  uint8_t type;
  uint8_t a;
  uint8_t b;
};

class Profile {
 public:
  // Loads the header and validates magic, schema and CRC. Writes the factory
  // defaults and returns false when anything fails validation.
  bool begin();

  Action action(uint8_t layer, uint8_t key, uint8_t gesture) const;
  Rgb paletteColor(uint8_t layer, uint8_t led) const;

  // Returns false when the slot is unused. `mask` is a bitmask of key indices.
  bool chord(uint8_t slot, uint8_t *mask, Action *action) const;

  uint8_t macroStepCount(uint8_t slot) const;
  MacroStep macroStep(uint8_t slot, uint8_t index) const;

  uint8_t brightness() const { return brightness_; }
  uint8_t baseLayer() const { return baseLayer_; }
  void setBrightness(uint8_t value) { brightness_ = value; }
  void setBaseLayer(uint8_t value) { baseLayer_ = value; }

  // Persists the runtime-tunable header fields without touching the body.
  void saveHeader();

  void writeDefaults();

  // ---- staged whole-profile transfer (see docs/PROTOCOL.md) ----------------
  //
  // Chunks land in a heap buffer, never in EEPROM, until the CRC checks out.
  // A cable yanked mid-transfer leaves the stored profile untouched.
  bool stageBegin(uint16_t byteCount, uint16_t crc);
  bool stageChunk(uint8_t sequence, const uint8_t *data, uint8_t length);
  bool stageCommit();
  void stageAbort();
  bool staging() const { return stage_ != NULL; }
  // Drops an abandoned transfer once MK_PROFILE_STAGE_TIMEOUT_MS has elapsed.
  void stageTick(uint32_t now);

  // Copies `length` bytes of the live profile out for a PROF read.
  void readRaw(uint16_t offset, uint8_t *out, uint16_t length) const;
  uint16_t bodyCrc() const;

 private:
  uint16_t keymapAddress(uint8_t layer, uint8_t key, uint8_t gesture) const;
  void writeHeaderFields(uint16_t crc);

  uint8_t brightness_ = MK_LED_DEFAULT_BRIGHTNESS;
  uint8_t baseLayer_ = 0;
  uint8_t flags_ = 0;

  uint8_t *stage_ = NULL;
  uint16_t stageBytes_ = 0;
  uint16_t stageCrc_ = 0;
  uint16_t stageReceived_ = 0;
  uint32_t stageStartedAt_ = 0;
};

// Payload bytes carried by one PROF data line. 48 bytes encode to 64 base64
// characters, which keeps the whole line inside MK_LINE_MAX.
#define MK_PROFILE_CHUNK_BYTES 48
