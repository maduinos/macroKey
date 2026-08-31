// The byte store the profile lives in.
//
// Profile talks only to these four calls, so moving to a part whose EEPROM is
// emulated in flash is a one-line change in Config.h rather than an edit to
// every read in Profile.cpp.
//
// The two families differ in a way that is invisible until it bites. AVR EEPROM
// is a real memory-mapped array: a read works from the first instruction and a
// write lands by itself. A flash-emulated store (RP2040, ESP32) is a RAM buffer
// that begin() has to fill before the first read and commit() has to write back
// after the last one -- miss either and the pad reads rubbish, or forgets
// everything the moment it is unplugged, with nothing failing anywhere.
#pragma once

#include <Arduino.h>
#include "Config.h"

void mkStoreBegin();
void mkStoreCommit();
uint8_t mkStoreRead(uint16_t address);
void mkStoreUpdate(uint16_t address, uint8_t value);
