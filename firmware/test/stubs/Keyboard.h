// Records every HID call instead of sending one, so a test can check that the
// firmware's output is balanced: a press with no release is a key the host
// believes is still held, which reads as the whole keyboard having died.
#pragma once

#include <stdio.h>

#include <Arduino.h>

#define KEY_LEFT_CTRL 0x80
#define KEY_LEFT_SHIFT 0x81
#define KEY_LEFT_ALT 0x82
#define KEY_LEFT_GUI 0x83
#define KEY_RIGHT_CTRL 0x84
#define KEY_RIGHT_SHIFT 0x85
#define KEY_RIGHT_ALT 0x86
#define KEY_RIGHT_GUI 0x87

struct KeyboardStub {
  void begin() {}
  void press(uint8_t c) { printf("key press %u\n", c); }
  void release(uint8_t c) { printf("key release %u\n", c); }
  void releaseAll() { printf("key release-all\n"); }
  void write(uint8_t c) { printf("type %u\n", c); }
};
extern KeyboardStub Keyboard;
