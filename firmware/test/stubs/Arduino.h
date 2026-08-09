// Minimal Arduino stubs, enough to type-check the macroKey firmware on a PC.
// Not a simulator: it exists so the compiler reads every line of the real
// sources. Behaviour is irrelevant, declarations are not.
#pragma once

#include <stdint.h>
#include <string.h>
#include <stdlib.h>

#define LOW 0
#define HIGH 1
#define INPUT_PULLUP 2
#define F(x) (x)
#define PROGMEM

typedef uint8_t byte;

inline void pinMode(uint8_t, uint8_t) {}
extern bool gPinLow[32];      // a pressed, active-low button
inline int digitalRead(uint8_t pin) { return gPinLow[pin & 31] ? LOW : HIGH; }
inline void digitalWrite(uint8_t, uint8_t) {}
extern uint32_t gClock;
extern uint32_t gClockStep;   // 0 lets a harness drive time itself
inline uint32_t millis() { gClock += gClockStep; return gClock; }
inline uint32_t micros() { return 0; }
inline void delay(uint32_t ms) { gClock += ms; }
inline void delayMicroseconds(uint32_t) {}
inline void noInterrupts() {}
inline void interrupts() {}

struct SerialStub {
  void begin(unsigned long) {}
  int available() { return 0; }
  int read() { return -1; }
  void print(const char *) {}
  void print(char) {}
  void print(int) {}
  void print(unsigned int) {}
  void print(long) {}
  void print(unsigned long) {}
  void println(const char *) {}
  void println(char) {}
  void println(int) {}
  void println(unsigned int) {}
  void println(long) {}
  void println(unsigned long) {}
  void println() {}
  void flush() {}
  operator bool() { return true; }
};
extern SerialStub Serial;

struct EEPROMStub {
  uint8_t data[1024];
  uint8_t read(int address) { return data[address & 1023]; }
  void write(int address, uint8_t value) { data[address & 1023] = value; }
  void update(int address, uint8_t value) { write(address, value); }
};
extern EEPROMStub EEPROM;

// AVR register names touched by firmware.ino's board-LED trick.
extern uint8_t DDRB, PORTB, DDRD, PORTD;
#define RXLED0 1
#define TXLED0 1

template <class T, class U> inline T min(T a, U b) { return a < (T)b ? a : (T)b; }
template <class T, class U> inline T max(T a, U b) { return a > (T)b ? a : (T)b; }
#define pgm_read_byte(addr) (*(const uint8_t *)(addr))
