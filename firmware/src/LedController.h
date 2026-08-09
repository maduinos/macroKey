// WS2812 output: composition, power limiting and the host watchdog.
//
// Three sources are composited, highest priority first:
//   1. key press flash   2. host ambient   3. profile scene
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

  void setBrightness(uint8_t value) { brightness_ = value; }
  uint8_t brightness() const { return brightness_; }

  // ---- keypress cues --------------------------------------------------------
  // Three answers to one question -- what did that press just do -- so they
  // share a slot and the newest wins. A hold that lands replaces the tap cue
  // that started it, which is exactly the reading you want.
  void notePress(uint8_t key, uint32_t now);
  //: The hold threshold was crossed while the key is still down.
  void noteHold(uint8_t key, uint32_t now);
  //: The key was received but nothing is bound to it.
  void noteUnbound(uint8_t key, uint32_t now);

  // ---- host controlled ambient --------------------------------------------
  // `timeoutMs` is how long the host promises to be worth waiting for. The
  // watchdog exists so a host that dies cannot freeze the pixel on its last
  // colour, but a host that is sitting on a colour picker is silent for far
  // longer than the default while being entirely alive -- so let it say so
  // rather than make it send traffic it has no reason to send. Zero keeps the
  // built-in default.
  void setHostMode(bool enabled, uint32_t now, uint16_t timeoutMs = 0);
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

  // A transient overlay above the scene: colour to blend toward, how far, and
  // for how long. Zero `startedAt` means no cue is showing.
  struct Cue {
    Rgb color;
    uint32_t startedAt;
    uint16_t durationMs;
    uint8_t amount;
  };

  void startCue(uint8_t key, Rgb color, uint16_t durationMs, uint8_t amount, uint32_t now);

  AmbientPixel ambient_[MK_LED_COUNT];
  Cue cue_[MK_LED_COUNT];
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
  uint16_t hostTimeoutMs_ = MK_LED_HOST_TIMEOUT_MS;
  uint8_t brightness_ = MK_LED_DEFAULT_BRIGHTNESS;
  bool hostMode_ = false;
  bool dirty_ = true;
};
