// WS2812 output: composition, power limiting and the host watchdog.
//
// Four sources are layered, highest priority first:
//   1. key press flash   2. layer tint   3. host ambient   4. profile scene
#pragma once

#include <Adafruit_NeoPixel.h>
#include <Arduino.h>

#include "Config.h"
#include "LedEffects.h"
#include "Profile.h"

class LedController {
 public:
  void begin(Profile *profile);
  void update(uint32_t now);

  void setActiveLayer(uint8_t layer) { activeLayer_ = layer; }
  void setBrightness(uint8_t value) { brightness_ = value; }
  uint8_t brightness() const { return brightness_; }
  void notePress(uint8_t key, uint32_t now);

  // ---- host controlled ambient layer --------------------------------------
  void setHostMode(bool enabled, uint32_t now);
  bool hostMode() const { return hostMode_; }
  void noteHostAlive(uint32_t now) { lastHostAt_ = now; }

  void setPixel(uint8_t index, Rgb color, uint8_t effect, uint16_t period, uint32_t now);
  void setAll(Rgb color, uint8_t effect, uint16_t period, uint32_t now);
  void setFrame(const Rgb *colors, uint8_t count, uint32_t now);
  // Lights `percent` of the strip as a progress bar, darkening the remainder.
  void setBar(uint8_t percent, Rgb color, uint32_t now);

 private:
  struct AmbientPixel {
    Rgb color;
    uint32_t startedAt;
    uint16_t period;
    uint8_t effect;
  };

  void render(uint32_t now);
  // Scales the whole frame down until the estimated draw fits the USB budget.
  void applyPowerLimit(Rgb *frame) const;

  Adafruit_NeoPixel strip_{MK_LED_COUNT, MK_LED_PIN, NEO_GRB + NEO_KHZ800};
  Profile *profile_ = NULL;

  AmbientPixel ambient_[MK_LED_COUNT];
  uint32_t pressedAt_[MK_LED_COUNT];
  Rgb lastShown_[MK_LED_COUNT];

  // Cross-fade state. `lastScene_`/`lastEffect_` are what the fade is compared
  // against, and `lastBase_` is the colour actually lit last frame, which is
  // where the next fade starts -- so a change part way through a fade continues
  // from what the eye is seeing rather than snapping back to the old scene.
  Rgb fadeFrom_[MK_LED_COUNT];
  Rgb lastBase_[MK_LED_COUNT];
  Rgb lastScene_[MK_LED_COUNT];
  uint32_t fadeStartedAt_[MK_LED_COUNT];
  uint8_t lastEffect_[MK_LED_COUNT];
  bool lastHostMode_ = false;

  uint32_t lastRenderAt_ = 0;
  uint32_t lastHostAt_ = 0;
  uint8_t activeLayer_ = 0;
  uint8_t brightness_ = MK_LED_DEFAULT_BRIGHTNESS;
  bool hostMode_ = false;
  bool dirty_ = true;
};
