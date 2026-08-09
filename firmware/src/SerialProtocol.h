// Line protocol v1 over USB CDC. See docs/PROTOCOL.md for the wire format.
//
// Nothing here touches HID or pins; it reads and writes state through Profile,
// KeyEngine and LedController.
#pragma once

#include <Arduino.h>

#include "Config.h"
#include "KeyEngine.h"
#include "LedController.h"
#include "Profile.h"

class SerialProtocol {
 public:
  void begin(Profile *profile, KeyEngine *engine, LedController *leds);
  void update(uint32_t now);

  void sendHello();
  void sendState();
  //: The pad asking the host to start or finish recording into this key.
  void sendRecordRequest(uint8_t key);
  void sendKeyEvent(uint8_t key, uint8_t gesture, uint8_t layer, bool released);
  void sendHostAction(uint8_t token, uint8_t key, uint8_t layer);

  // Diagnostics, off by default so the link stays quiet in normal use.
  bool debugEnabled() const { return debug_; }
  void sendLog(char level, const char *message);

 private:
  void handleLine(uint32_t now);
  bool parseLine();
  const char *arg(const char *key) const;
  bool argUInt(const char *key, uint32_t *out) const;
  bool argHex(const char *key, uint32_t *out) const;
  bool argColor(const char *key, Rgb *out) const;

  void cmdLed(uint32_t now);
  void cmdLayer();
  void cmdProfile(uint32_t now);
  void profileDump();

  void sendOk();
  void sendErr(const char *code);
  void writeId();

  static const uint8_t MAX_ARGS = 8;

  Profile *profile_ = NULL;
  KeyEngine *engine_ = NULL;
  LedController *leds_ = NULL;

  char line_[MK_LINE_MAX + 1];
  uint8_t lineLength_ = 0;
  bool lineOverflow_ = false;

  const char *verb_ = NULL;
  const char *sub_ = NULL;
  const char *argKeys_[MAX_ARGS];
  const char *argValues_[MAX_ARGS];
  uint8_t argCount_ = 0;
  const char *id_ = NULL;

  bool debug_ = false;
};
