#include "LedEffects.h"

#include <string.h>

namespace {

void scale(Rgb *color, uint8_t factor) {
  color->r = (uint8_t)(((uint16_t)color->r * factor) >> 8);
  color->g = (uint8_t)(((uint16_t)color->g * factor) >> 8);
  color->b = (uint8_t)(((uint16_t)color->b * factor) >> 8);
}

// Triangle wave 0..255 across one period. Cheaper than a sine table and
// visually indistinguishable once it drives an LED.
uint8_t triangle(uint32_t elapsed, uint16_t period) {
  if (period == 0) return 255;
  uint16_t phase = (uint16_t)(elapsed % period);
  uint32_t half = period / 2;
  if (half == 0) return 255;
  if (phase < half) return (uint8_t)((uint32_t)phase * 255 / half);
  return (uint8_t)((uint32_t)(period - phase) * 255 / half);
}

void fxSolid(Rgb *, uint32_t, uint16_t, uint8_t) {}

void fxBreathe(Rgb *color, uint32_t elapsed, uint16_t period, uint8_t) {
  // Floors at 15% so a breathing pixel never reads as "off".
  uint8_t wave = triangle(elapsed, period == 0 ? 2000 : period);
  scale(color, (uint8_t)(38 + ((uint16_t)wave * 217 >> 8)));
}

void fxPulse(Rgb *color, uint32_t elapsed, uint16_t period, uint8_t) {
  uint16_t effective = period == 0 ? 800 : period;
  uint8_t wave = triangle(elapsed, effective);
  scale(color, (uint8_t)(((uint16_t)wave * wave) >> 8));  // squared: snappier
}

void fxBlink(Rgb *color, uint32_t elapsed, uint16_t period, uint8_t) {
  uint16_t effective = period == 0 ? 600 : period;
  if ((elapsed % effective) >= (uint32_t)effective / 2) {
    color->r = color->g = color->b = 0;
  }
}

void fxFlash(Rgb *color, uint32_t elapsed, uint16_t period, uint8_t) {
  // One-shot: full brightness, linear decay, then dark and staying dark.
  uint16_t effective = period == 0 ? 400 : period;
  if (elapsed >= effective) {
    color->r = color->g = color->b = 0;
    return;
  }
  scale(color, (uint8_t)(255 - (uint32_t)elapsed * 255 / effective));
}

// 0..767 hue ramp through red -> green -> blue, no float and no table.
Rgb hueRamp(uint16_t position) {
  position %= 768;
  uint8_t phase = (uint8_t)(position / 256);
  uint8_t step = (uint8_t)(position % 256);
  switch (phase) {
    case 0: return Rgb{(uint8_t)(255 - step), step, 0};
    case 1: return Rgb{0, (uint8_t)(255 - step), step};
    default: return Rgb{step, 0, (uint8_t)(255 - step)};
  }
}

void fxRainbow(Rgb *color, uint32_t elapsed, uint16_t period, uint8_t index) {
  uint16_t effective = period == 0 ? 3000 : period;
  uint16_t base = (uint16_t)((elapsed % effective) * 768 / effective);
  Rgb hue = hueRamp((uint16_t)(base + index * (768 / 8)));
  // Keeps the caller's colour as an intensity envelope over the hue sweep.
  uint8_t level = max(max(color->r, color->g), color->b);
  *color = hue;
  scale(color, level);
}

}  // namespace

const LedEffectDef MK_LED_EFFECTS[FX_COUNT] = {
    {"solid", fxSolid},
    {"breathe", fxBreathe},
    {"pulse", fxPulse},
    {"blink", fxBlink},
    {"flash", fxFlash},
    {"rainbow", fxRainbow},
};

uint8_t mkLedEffectByName(const char *name) {
  if (name == NULL) return FX_COUNT;
  for (uint8_t i = 0; i < FX_COUNT; i++) {
    if (strcmp(name, MK_LED_EFFECTS[i].name) == 0) return i;
  }
  return FX_COUNT;
}
