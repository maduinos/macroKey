#include "SerialProtocol.h"

#include <avr/wdt.h>
#include <stdlib.h>
#include <string.h>

#include "HidBackend.h"
#include "Util.h"

namespace {

// Caterina (the Leonardo bootloader) checks this RAM word after a watchdog
// reset and stays in the bootloader when it holds the magic value.
uint16_t *const kBootKeyPtr = (uint16_t *)0x0800;
const uint16_t kBootKey = 0x7777;

void enterBootloader() {
  *kBootKeyPtr = kBootKey;
  wdt_enable(WDTO_15MS);
  for (;;) {
  }
}

}  // namespace

void SerialProtocol::begin(Profile *profile, KeyEngine *engine, LedController *leds) {
  profile_ = profile;
  engine_ = engine;
  leds_ = leds;
  Serial.begin(MK_SERIAL_BAUD);
}

// ------------------------------------------------------------------ output --

void SerialProtocol::writeId() {
  if (id_ == NULL) return;
  Serial.print(F(" id="));
  Serial.print(id_);
}

void SerialProtocol::sendOk() {
  Serial.print(F("OK"));
  writeId();
  Serial.println();
}

void SerialProtocol::sendErr(const char *code) {
  Serial.print(F("ERR"));
  writeId();
  Serial.print(F(" code="));
  Serial.println(code);
}

void SerialProtocol::sendHello() {
  Serial.print(F("HELLO proto="));
  Serial.print(MK_PROTOCOL_VERSION);
  Serial.print(F(" fw=" MK_FIRMWARE_VERSION " board=" MK_BOARD_NAME " keys="));
  Serial.print(MK_KEY_COUNT);
  Serial.print(F(" leds="));
  Serial.print(MK_LED_COUNT);
  Serial.print(F(" bytes="));
  Serial.println(MK_PROFILE_SIZE);
}

void SerialProtocol::sendState() {
  Serial.print(F("STATE bright="));
  Serial.print(leds_->brightness());
  Serial.print(F(" ledmode="));
  Serial.print(leds_->hostMode() ? F("host") : F("local"));
  Serial.print(F(" hid="));
  Serial.print(engine_->hidEnabled() ? 1 : 0);
  Serial.print(F(" up="));
  Serial.println(millis());
}

void SerialProtocol::sendRecordRequest(uint8_t key, uint8_t gesture) {
  Serial.print(F("EV t=record k="));
  Serial.print(key);
  Serial.print(F(" g="));
  Serial.print(gestureName(gesture));
  Serial.print(F(" ms="));
  Serial.println(millis());
}

void SerialProtocol::sendKeyEvent(uint8_t key, uint8_t gesture, bool released) {
  Serial.print(F("EV t=key k="));
  Serial.print(key);
  Serial.print(F(" g="));
  Serial.print(released ? "holdend" : gestureName(gesture));
  Serial.print(F(" ms="));
  Serial.println(millis());
}

void SerialProtocol::sendLog(char level, const char *message) {
  if (!debug_) return;
  char encoded[64];
  uint16_t length = (uint16_t)strlen(message);
  if (length > 42) length = 42;  // keeps the encoded line under MK_LINE_MAX
  mkBase64Encode((const uint8_t *)message, length, encoded);
  Serial.print(F("LOG lvl="));
  Serial.print(level);
  Serial.print(F(" msg="));
  Serial.println(encoded);
}

// ------------------------------------------------------------------ parsing --

bool SerialProtocol::parseLine() {
  verb_ = NULL;
  sub_ = NULL;
  id_ = NULL;
  argCount_ = 0;

  char *cursor = line_;
  while (*cursor != '\0') {
    while (*cursor == ' ') cursor++;
    if (*cursor == '\0') break;

    char *token = cursor;
    while (*cursor != '\0' && *cursor != ' ') cursor++;
    if (*cursor != '\0') *cursor++ = '\0';

    if (verb_ == NULL) {
      verb_ = token;
      continue;
    }

    char *equals = strchr(token, '=');
    if (equals == NULL) {
      // Bare positional word such as the `all` in `LED all rgb=...`.
      if (sub_ == NULL) sub_ = token;
      continue;
    }
    *equals = '\0';
    if (strcmp(token, "id") == 0) {
      id_ = equals + 1;
      continue;
    }
    // Extra pairs beyond MAX_ARGS are dropped rather than rejected: forward
    // compatibility says unknown keys are ignorable.
    if (argCount_ < MAX_ARGS) {
      argKeys_[argCount_] = token;
      argValues_[argCount_] = equals + 1;
      argCount_++;
    }
  }
  return verb_ != NULL;
}

const char *SerialProtocol::arg(const char *key) const {
  for (uint8_t i = 0; i < argCount_; i++) {
    if (strcmp(argKeys_[i], key) == 0) return argValues_[i];
  }
  return NULL;
}

bool SerialProtocol::argUInt(const char *key, uint32_t *out) const {
  const char *value = arg(key);
  if (value == NULL || *value == '\0') return false;
  char *end = NULL;
  unsigned long parsed = strtoul(value, &end, 10);
  if (end == value || *end != '\0') return false;
  *out = (uint32_t)parsed;
  return true;
}

bool SerialProtocol::argHex(const char *key, uint32_t *out) const {
  const char *value = arg(key);
  return value != NULL && mkParseHex(value, out, 8);
}

bool SerialProtocol::argColor(const char *key, Rgb *out) const {
  const char *value = arg(key);
  if (value == NULL || strlen(value) != 6) return false;
  uint32_t packed;
  if (!mkParseHex(value, &packed, 6)) return false;
  out->r = (uint8_t)(packed >> 16);
  out->g = (uint8_t)(packed >> 8);
  out->b = (uint8_t)packed;
  return true;
}

// ----------------------------------------------------------------- commands --

void SerialProtocol::cmdLed(uint32_t now) {
  const char *mode = arg("mode");
  if (mode != NULL) {
    if (strcmp(mode, "host") == 0) {
      // Optional `ms=`: how long this host will go quiet before it should be
      // presumed gone. A host that keeps a frame on screen while someone reads
      // a colour picker knows it will be silent for far longer than the default
      // and can say so, instead of sending keepalives with nothing to report.
      uint32_t timeout = 0;
      if (argUInt("ms", &timeout)) {
        if (timeout > MK_LED_HOST_TIMEOUT_MAX_MS) {
          sendErr("range");
          return;
        }
      }
      leds_->setHostMode(true, now, (uint16_t)timeout);
    } else if (strcmp(mode, "local") == 0) {
      leds_->setHostMode(false, now);
    } else {
      sendErr("arg");
      return;
    }
    sendOk();
    return;
  }

  uint32_t number;
  if (argUInt("bright", &number)) {
    if (number > 255) {
      sendErr("range");
      return;
    }
    leds_->setBrightness((uint8_t)number);
    profile_->setBrightness((uint8_t)number);
    leds_->noteHostAlive(now);
    sendOk();
    return;
  }

  const char *frame = arg("frame");
  if (frame != NULL) {
    Rgb colors[MK_LED_COUNT];
    uint8_t count = 0;
    const char *cursor = frame;
    while (*cursor != '\0' && count < MK_LED_COUNT) {
      uint32_t packed;
      char hex[7];
      uint8_t length = 0;
      while (cursor[length] != '\0' && cursor[length] != ',' && length < 6) {
        hex[length] = cursor[length];
        length++;
      }
      hex[length] = '\0';
      if (length != 6 || !mkParseHex(hex, &packed, 6)) {
        sendErr("arg");
        return;
      }
      colors[count++] = Rgb{(uint8_t)(packed >> 16), (uint8_t)(packed >> 8), (uint8_t)packed};
      cursor += length;
      if (*cursor == ',') cursor++;
    }
    leds_->setFrame(colors, count, now);
    sendOk();
    return;
  }

  Rgb color;
  if (argUInt("bar", &number)) {
    if (!argColor("rgb", &color)) color = Rgb{0, 255, 128};
    leds_->setBar((uint8_t)number, color, now);
    sendOk();
    return;
  }

  if (!argColor("rgb", &color)) {
    sendErr("arg");
    return;
  }
  uint8_t effect = FX_SOLID;
  const char *effectName = arg("fx");
  if (effectName != NULL) {
    effect = mkLedEffectByName(effectName);
    if (effect >= FX_COUNT) {
      sendErr("arg");
      return;
    }
  }
  uint32_t period = 0;
  argUInt("ms", &period);

  if (sub_ != NULL && strcmp(sub_, "all") == 0) {
    leds_->setAll(color, effect, (uint16_t)period, now);
    sendOk();
    return;
  }
  if (argUInt("i", &number) && number < MK_LED_COUNT) {
    leds_->setPixel((uint8_t)number, color, effect, (uint16_t)period, now);
    sendOk();
    return;
  }
  sendErr("arg");
}

void SerialProtocol::profileDump() {
  uint8_t buffer[MK_PROFILE_CHUNK_BYTES];
  char encoded[((MK_PROFILE_CHUNK_BYTES + 2) / 3) * 4 + 1];
  char crcHex[5];
  mkFormatHex(profile_->bodyCrc(), 4, crcHex);

  Serial.print(F("PROF begin bytes="));
  Serial.print(MK_PROFILE_SIZE);
  Serial.print(F(" crc="));
  Serial.println(crcHex);

  uint8_t sequence = 0;
  for (uint16_t offset = 0; offset < MK_PROFILE_SIZE; offset += MK_PROFILE_CHUNK_BYTES) {
    uint16_t length = MK_PROFILE_SIZE - offset;
    if (length > MK_PROFILE_CHUNK_BYTES) length = MK_PROFILE_CHUNK_BYTES;
    profile_->readRaw(offset, buffer, length);
    mkBase64Encode(buffer, length, encoded);
    Serial.print(F("PROF data seq="));
    Serial.print(sequence++);
    Serial.print(F(" b64="));
    Serial.println(encoded);
  }
  Serial.println(F("PROF end"));
}

void SerialProtocol::cmdProfile(uint32_t now) {
  (void)now;
  if (sub_ == NULL) {
    sendErr("arg");
    return;
  }

  if (strcmp(sub_, "read") == 0) {
    profileDump();
    return;
  }

  if (strcmp(sub_, "begin") == 0) {
    uint32_t bytes = 0;
    uint32_t crc = 0;
    if (!argUInt("bytes", &bytes) || !argHex("crc", &crc)) {
      sendErr("arg");
      return;
    }
    if (bytes != MK_PROFILE_SIZE) {
      sendErr("range");
      return;
    }
    if (profile_->stageBegin((uint16_t)bytes, (uint16_t)crc)) {
      sendOk();
    } else {
      sendErr("nomem");
    }
    return;
  }

  if (strcmp(sub_, "data") == 0) {
    uint32_t sequence = 0;
    const char *encoded = arg("b64");
    if (!argUInt("seq", &sequence) || encoded == NULL || sequence > 255) {
      sendErr("arg");
      return;
    }
    uint8_t decoded[MK_PROFILE_CHUNK_BYTES];
    int16_t length =
        mkBase64Decode(encoded, (uint16_t)strlen(encoded), decoded, sizeof(decoded));
    if (length < 0) {
      sendErr("arg");
      return;
    }
    if (!profile_->stageChunk((uint8_t)sequence, decoded, (uint8_t)length)) {
      sendErr("range");
      return;
    }
    sendOk();
    return;
  }

  if (strcmp(sub_, "commit") == 0) {
    if (profile_->stageCommit()) {
      engine_->noteProfileChanged();
      sendOk();
    } else {
      sendErr("crc");
    }
    return;
  }

  if (strcmp(sub_, "abort") == 0) {
    profile_->stageAbort();
    sendOk();
    return;
  }

  sendErr("arg");
}

void SerialProtocol::handleLine(uint32_t now) {
  if (lineOverflow_) {
    lineOverflow_ = false;
    sendErr("long");
    return;
  }
  if (!parseLine()) return;

  if (strcmp(verb_, "PING") == 0) {
    leds_->noteHostAlive(now);
    sendOk();
  } else if (strcmp(verb_, "IDENT") == 0) {
    sendHello();
  } else if (strcmp(verb_, "STATE?") == 0) {
    sendState();
  } else if (strcmp(verb_, "LED") == 0) {
    cmdLed(now);
  } else if (strcmp(verb_, "PROF") == 0) {
    cmdProfile(now);
  } else if (strcmp(verb_, "MOUSE") == 0 && sub_ != NULL && strcmp(sub_, "home") == 0) {
    // Asked for when a recording starts. Everything the recorder then sees is
    // measured from the corner, which is what lets a replayed macro land where
    // it was recorded rather than an unknown distance away from it.
    if (!engine_->hidEnabled()) {
      sendErr("busy");  // still inside the boot grace window
    } else {
      mkMouseHome();
      sendOk();
    }
  } else if (strcmp(verb_, "SAVE") == 0) {
    profile_->saveHeader();
    sendOk();
  } else if (strcmp(verb_, "RESET") == 0) {
    uint32_t defaults = 0;
    if (argUInt("defaults", &defaults) && defaults == 1) {
      profile_->writeDefaults();
      sendOk();
    } else {
      sendErr("arg");
    }
  } else if (strcmp(verb_, "DBG") == 0) {
    uint32_t on = 0;
    argUInt("on", &on);
    debug_ = on != 0;
    sendOk();
  } else if (strcmp(verb_, "BOOT") == 0) {
    sendOk();
    Serial.flush();
    enterBootloader();
  } else {
    sendErr("verb");
  }
}

void SerialProtocol::update(uint32_t now) {
  profile_->stageTick(now);

  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      line_[lineLength_] = '\0';
      handleLine(now);
      lineLength_ = 0;
      continue;
    }
    if (lineLength_ >= MK_LINE_MAX) {
      // Keep consuming until the newline so the next line is not corrupted by
      // the tail of this one.
      lineOverflow_ = true;
      continue;
    }
    line_[lineLength_++] = c;
  }
}
