// LED animation kernels.
//
// Adding an effect is one function plus one row in MK_LED_EFFECTS. The row
// carries the protocol name too, so a new effect is parseable from `fx=` the
// moment it exists -- there is no second place to update.
#pragma once

#include <Arduino.h>

#include "Profile.h"

// Mutates `color` in place for the current animation phase.
//   elapsed - milliseconds since the effect started on this pixel
//   period  - full cycle length in milliseconds
//   index   - pixel index, so effects can stagger across the strip
typedef void (*LedEffectFn)(Rgb *color, uint32_t elapsed, uint16_t period, uint8_t index);

struct LedEffectDef {
  const char *name;
  LedEffectFn fn;
};

enum LedEffectId : uint8_t {
  FX_SOLID = 0,
  FX_BREATHE,
  FX_PULSE,
  FX_BLINK,
  FX_FLASH,
  FX_RAINBOW,
  FX_COUNT
};

extern const LedEffectDef MK_LED_EFFECTS[FX_COUNT];

// Returns FX_COUNT when the name is unknown.
uint8_t mkLedEffectByName(const char *name);
