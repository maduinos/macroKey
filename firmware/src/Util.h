// Small encoding helpers shared by the profile store and the serial protocol.
#pragma once

#include <Arduino.h>

uint16_t mkCrc16(const uint8_t *data, uint16_t length, uint16_t seed = 0xFFFF);

// Parses up to `maxNibbles` hex digits. Returns false on any non-hex byte.
bool mkParseHex(const char *text, uint32_t *out, uint8_t maxNibbles = 8);

// Writes `value` as exactly `nibbles` uppercase hex digits plus a terminator.
void mkFormatHex(uint32_t value, uint8_t nibbles, char *out);

// Standard base64 without padding tolerance concerns: `in` must be a multiple
// of four characters. Returns the number of bytes written, or -1 on bad input.
int16_t mkBase64Decode(const char *in, uint16_t inLength, uint8_t *out, uint16_t outCapacity);

// Encodes `length` bytes (padded with '='). `out` needs 4*ceil(length/3)+1 bytes.
uint16_t mkBase64Encode(const uint8_t *in, uint16_t length, char *out);
