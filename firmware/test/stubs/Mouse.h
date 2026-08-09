#pragma once

#include <stdio.h>

#include <Arduino.h>

struct MouseStub {
  void begin() {}
  void click(uint8_t mask) { printf("mouse click %u\n", mask); }
  void press(uint8_t mask) { printf("mouse press %u\n", mask); }
  void release(uint8_t mask) { printf("mouse release %u\n", mask); }
  void move(int8_t x, int8_t y, int8_t wheel) {
    if (wheel) {
      printf("mouse wheel %d\n", wheel);
    } else {
      printf("mouse move %d %d\n", x, y);
    }
  }
};
extern MouseStub Mouse;
