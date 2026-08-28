// Minimal Arduino stubs, enough to run the macroKey firmware on a PC.
// Not a simulator: it exists so the compiler reads every line of the real
// sources. Most of it only needs to have the right declarations -- the
// exceptions are the pins, the clock, the EEPROM array and Serial, which the
// harness drives and reads back, so those four behave.
#pragma once

#include <stdint.h>
#include <stdio.h>
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

// Enough of a port to drive SerialProtocol: canned input on one side, a
// transcript on the other. The firmware's own parser and command handlers then
// run for real, which is the only way to test what a line actually does to the
// device rather than what it was meant to do.
struct SerialStub {
  const char *in = "";
  unsigned in_position = 0;
  char out[8192] = {0};
  unsigned out_length = 0;

  void begin(unsigned long) {}

  // Queues a line (or several) as if the host had sent it.
  void feed(const char *text) {
    in = text;
    in_position = 0;
  }

  int available() { return in[in_position] != '\0' ? 1 : 0; }
  int read() { return in[in_position] != '\0' ? in[in_position++] : -1; }

  void emit(const char *text) {
    while (*text != '\0' && out_length + 1 < sizeof(out)) out[out_length++] = *text++;
    out[out_length] = '\0';
  }
  void emit_number(long value) {
    char buffer[24];
    snprintf(buffer, sizeof(buffer), "%ld", value);
    emit(buffer);
  }

  void print(const char *text) { emit(text); }
  void print(char value) { char text[2] = {value, '\0'}; emit(text); }
  void print(int value) { emit_number(value); }
  void print(unsigned int value) { emit_number((long)value); }
  void print(long value) { emit_number(value); }
  void print(unsigned long value) { emit_number((long)value); }
  template <class T> void println(T value) { print(value); emit("\n"); }
  void println() { emit("\n"); }
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
