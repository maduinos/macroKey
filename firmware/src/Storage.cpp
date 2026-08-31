#include "Storage.h"

#if defined(ARDUINO_ARCH_RP2040)

#include <LittleFS.h>

namespace {
const char *const kProfilePath = "/macrokey-profile.bin";
const char *const kStagePath = "/macrokey-profile.tmp";
uint8_t gStore[MK_EEPROM_SIZE];
bool gMounted = false;
bool gDirty = false;
}  // namespace

void mkStoreBegin() {
  memset(gStore, 0xFF, sizeof(gStore));
  gMounted = LittleFS.begin();
  if (!gMounted) return;

  File profile = LittleFS.open(kProfilePath, "r");
  if (!profile || profile.size() != (int)sizeof(gStore)) return;
  if (profile.read(gStore, sizeof(gStore)) != (int)sizeof(gStore)) {
    memset(gStore, 0xFF, sizeof(gStore));
  }
  profile.close();
}

void mkStoreCommit() {
  if (!gMounted || !gDirty) return;
  LittleFS.remove(kStagePath);
  File staged = LittleFS.open(kStagePath, "w");
  if (!staged) return;
  bool complete = staged.write(gStore, sizeof(gStore)) == sizeof(gStore);
  staged.flush();
  staged.close();
  if (!complete) {
    LittleFS.remove(kStagePath);
    return;
  }
  if (LittleFS.rename(kStagePath, kProfilePath)) gDirty = false;
}

uint8_t mkStoreRead(uint16_t address) {
  return address < sizeof(gStore) ? gStore[address] : 0;
}

void mkStoreUpdate(uint16_t address, uint8_t value) {
  if (address >= sizeof(gStore) || gStore[address] == value) return;
  gStore[address] = value;
  gDirty = true;
}

#else

#include <EEPROM.h>

void mkStoreBegin() {}
void mkStoreCommit() {}
uint8_t mkStoreRead(uint16_t address) { return EEPROM.read(address); }
void mkStoreUpdate(uint16_t address, uint8_t value) { EEPROM.update(address, value); }

#endif
