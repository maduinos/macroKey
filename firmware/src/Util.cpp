#include "Util.h"

// CRC-16/CCITT-FALSE. Bitwise rather than table driven: the profile blob is
// only ~1 KB and a 512 byte table would cost more flash than the loop saves.
uint16_t mkCrc16(const uint8_t *data, uint16_t length, uint16_t seed) {
  uint16_t crc = seed;
  for (uint16_t i = 0; i < length; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (uint8_t bit = 0; bit < 8; bit++) {
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
  }
  return crc;
}

static int8_t hexValue(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

bool mkParseHex(const char *text, uint32_t *out, uint8_t maxNibbles) {
  if (text == NULL || *text == '\0') return false;
  uint32_t value = 0;
  uint8_t count = 0;
  for (const char *p = text; *p != '\0'; p++) {
    int8_t nibble = hexValue(*p);
    if (nibble < 0) return false;
    if (++count > maxNibbles) return false;
    value = (value << 4) | (uint8_t)nibble;
  }
  *out = value;
  return true;
}

void mkFormatHex(uint32_t value, uint8_t nibbles, char *out) {
  static const char digits[] = "0123456789ABCDEF";
  for (int8_t i = nibbles - 1; i >= 0; i--) {
    out[i] = digits[value & 0x0F];
    value >>= 4;
  }
  out[nibbles] = '\0';
}

static const char kB64Alphabet[] PROGMEM =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static int8_t base64Value(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

int16_t mkBase64Decode(const char *in, uint16_t inLength, uint8_t *out, uint16_t outCapacity) {
  if ((inLength & 0x03) != 0) return -1;
  uint16_t written = 0;
  for (uint16_t i = 0; i < inLength; i += 4) {
    int8_t v[4];
    uint8_t padding = 0;
    for (uint8_t j = 0; j < 4; j++) {
      char c = in[i + j];
      if (c == '=') {
        // Padding is only legal in the final quantum, trailing positions only.
        if (i + 4 != inLength || j < 2) return -1;
        v[j] = 0;
        padding++;
      } else {
        v[j] = base64Value(c);
        if (v[j] < 0 || padding != 0) return -1;
      }
    }
    uint8_t bytes = 3 - padding;
    if (written + bytes > outCapacity) return -1;
    uint32_t chunk = ((uint32_t)v[0] << 18) | ((uint32_t)v[1] << 12) |
                     ((uint32_t)v[2] << 6) | (uint32_t)v[3];
    if (bytes > 0) out[written++] = (uint8_t)(chunk >> 16);
    if (bytes > 1) out[written++] = (uint8_t)(chunk >> 8);
    if (bytes > 2) out[written++] = (uint8_t)chunk;
  }
  return (int16_t)written;
}

uint16_t mkBase64Encode(const uint8_t *in, uint16_t length, char *out) {
  uint16_t written = 0;
  for (uint16_t i = 0; i < length; i += 3) {
    uint8_t remaining = (uint8_t)min((uint16_t)3, (uint16_t)(length - i));
    uint32_t chunk = (uint32_t)in[i] << 16;
    if (remaining > 1) chunk |= (uint32_t)in[i + 1] << 8;
    if (remaining > 2) chunk |= (uint32_t)in[i + 2];

    for (uint8_t j = 0; j < 4; j++) {
      if (j <= remaining) {
        uint8_t index = (uint8_t)((chunk >> (18 - 6 * j)) & 0x3F);
        out[written++] = (char)pgm_read_byte(&kB64Alphabet[index]);
      } else {
        out[written++] = '=';
      }
    }
  }
  out[written] = '\0';
  return written;
}
