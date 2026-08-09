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
// Bumped from 1 with the layout below. The profile is the same 1024 bytes
// either way, so without this a pad holding the old arrangement would be read
// as nonsense rather than recognised as out of date and rewritten.
#define MK_PROFILE_SCHEMA 2

// Flat EEPROM layout. Every region is a fixed-stride array so lookups are
// address arithmetic with no scanning.
static const uint16_t MK_HEADER_OFFSET = 0;
static const uint16_t MK_HEADER_SIZE = 16;
static const uint16_t MK_KEYMAP_OFFSET = MK_HEADER_OFFSET + MK_HEADER_SIZE;
static const uint16_t MK_KEYMAP_SIZE =
    (uint16_t)MK_LAYER_COUNT * MK_KEY_COUNT * MK_KEYMAP_GESTURES * sizeof(Action);
// No chord region: see MK_MACRO_SLOTS in Config.h.
static const uint16_t MK_PALETTE_OFFSET = MK_KEYMAP_OFFSET + MK_KEYMAP_SIZE;
static const uint16_t MK_PALETTE_SIZE = (uint16_t)MK_LAYER_COUNT * MK_LED_COUNT * 3;
static const uint16_t MK_MACRO_OFFSET = MK_PALETTE_OFFSET + MK_PALETTE_SIZE;
// One byte per slot: how many records it uses. There is no stored offset --
// slots are packed in order, so a slot's start is the sum of the counts before
// it (see Profile::macroBase). That is 32 bytes back on a 1 KB part, and it
// retires a whole class of bug: an index and a region that disagreed about
// where a macro began.
static const uint16_t MK_MACRO_INDEX_SIZE = MK_MACRO_SLOTS;
static const uint16_t MK_MACRO_RECORD_SIZE = 3;

// The ATmega32u4 has exactly this much EEPROM, and the profile is the only
// thing in it.
static const uint16_t MK_EEPROM_SIZE = 1024;
// Everything left after the fixed regions. Derived rather than written down: it
// was 480, then 768, each time by hand and each time leaving bytes unused, and
// macro records are the one thing a pad with eight keys and no layers wants
// more of. Whatever the header, keymap and palette cost, macros get the rest.
static const uint16_t MK_MACRO_REGION_SIZE = MK_EEPROM_SIZE - MK_MACRO_OFFSET;
static const uint16_t MK_PROFILE_SIZE = MK_MACRO_OFFSET + MK_MACRO_REGION_SIZE;
static const uint16_t MK_MACRO_RECORD_CAPACITY =
    (MK_MACRO_REGION_SIZE - MK_MACRO_INDEX_SIZE) / MK_MACRO_RECORD_SIZE;

static_assert(MK_PROFILE_SIZE == MK_EEPROM_SIZE, "the profile is meant to be the whole EEPROM");
// A slot's record count is one byte, so no macro may run past 255 records
// however much room the region has, and every slot must be reachable.
static_assert(MK_MACRO_MAX_RECORDS <= 255, "a slot's record count is one byte");
static_assert(MK_MACRO_RECORD_CAPACITY >= MK_MACRO_MAX_RECORDS,
              "one full slot must fit in the region");

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

  uint8_t macroRecordCount(uint8_t slot) const;
  // Where a slot's records start, in records from the base of the region. Sums
  // the counts of the slots before it, so read it once per macro rather than
  // once per record.
  uint16_t macroBase(uint8_t slot) const;
  // The three raw bytes of one record. Raw on purpose: the payload of a text
  // run is ASCII, and sanitising it against the action table would corrupt it.
  // Callers that expect an action check the type themselves.
  MacroStep macroRecord(uint16_t base, uint8_t index) const;

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
  //
  // That buffer is MK_PROFILE_SIZE -- 1024 of the ATmega32u4's 2560 bytes --
  // and it is held for the whole transfer, which now happens every time a
  // recording is saved. The margin, measured rather than assumed:
  //
  //   free at runtime (2560 - globals)                    1509 B
  //   staging buffer                                     -1024 B
  //   deepest write path: update 31, parseLine 18,
  //     cmdProfile 60, mkBase64Decode 26, + call frames   ~ -165 B
  //   USB CDC interrupt frames                            ~ -120 B
  //   left                                               ~  200 B
  //
  // A `PROF read` arriving mid-transfer adds profileDump's 131 B on top, which
  // still fits; the host serialises the two anyway. Numbers from
  // `arduino-cli compile -fstack-usage` with LTO off.
  //
  // AVR has no MMU, so overrunning this corrupts globals silently rather than
  // faulting. If a change grows the globals or a frame, re-measure: the failure
  // it would produce is a pad that occasionally mangles a profile while saving,
  // which is close to undiagnosable on a device with one pixel. A malloc that
  // outright fails is the safe case -- stageBegin returns false and the host is
  // told `nomem`.
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
