#pragma once
#include <Arduino.h>
#define NEO_GRB 0
#define NEO_KHZ800 0
class Adafruit_NeoPixel {
 public:
  Adafruit_NeoPixel(uint16_t, uint8_t, uint8_t) {}
  void begin() {}
  void show() {}
  void clear() {}
  void setBrightness(uint8_t) {}
  void setPixelColor(uint16_t, uint8_t, uint8_t, uint8_t) {}
  void setPixelColor(uint16_t, uint32_t) {}
  static uint32_t Color(uint8_t, uint8_t, uint8_t) { return 0; }
  uint16_t numPixels() const { return 1; }
};
