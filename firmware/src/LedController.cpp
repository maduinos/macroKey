#include "LedController.h"

namespace {

const uint16_t RENDER_INTERVAL_MS = 1000 / MK_LED_FPS;

// A WS2812 channel at full scale draws roughly 20 mA.
const uint16_t MILLIAMPS_PER_CHANNEL_X255 = 20;
// Quiescent draw of the driver in each package, regardless of colour.
const uint16_t IDLE_MILLIAMPS_PER_PIXEL = 1;

Rgb blendToward(Rgb color, Rgb target, uint8_t amount) {
  color.r = (uint8_t)(color.r + (((int16_t)target.r - color.r) * amount >> 8));
  color.g = (uint8_t)(color.g + (((int16_t)target.g - color.g) * amount >> 8));
  color.b = (uint8_t)(color.b + (((int16_t)target.b - color.b) * amount >> 8));
  return color;
}

}  // namespace

void LedController::begin(Profile *profile) {
  profile_ = profile;
  brightness_ = profile->brightness();
  strip_.begin();
  strip_.clear();
  strip_.show();
  for (uint8_t i = 0; i < MK_LED_COUNT; i++) {
    ambient_[i] = AmbientPixel{{0, 0, 0}, 0, 0, FX_SOLID};
    cue_[i] = Cue{Rgb{0, 0, 0}, 0, 0, 0};
    lastShown_[i] = Rgb{0, 0, 0};
    fadeFrom_[i] = Rgb{0, 0, 0};
    lastBase_[i] = Rgb{0, 0, 0};
    lastScene_[i] = Rgb{0, 0, 0};
    fadeStartedAt_[i] = 0;
    lastEffect_[i] = FX_SOLID;
  }
}

void LedController::startCue(uint8_t key, Rgb color, uint16_t durationMs, uint8_t amount,
                             uint32_t now) {
  // With one pixel per key each press lights its own. With fewer pixels than
  // keys the surplus keys fold onto the last one, so every key still gives
  // feedback -- dropping the press outright would leave most of the pad dark.
  uint8_t index = key < MK_LED_COUNT ? key : MK_LED_COUNT - 1;
  // `now` is millis(), which is zero only in the first millisecond after boot,
  // and HID is suppressed for MK_BOOT_GRACE_MS anyway. Nudging it off zero
  // keeps "no cue" representable without a second flag.
  cue_[index] = Cue{color, now == 0 ? 1 : now, durationMs, amount};
}

void LedController::notePress(uint8_t key, uint32_t now) {
  startCue(key, Rgb{255, 255, 255}, MK_LED_PRESS_FLASH_MS, MK_LED_PRESS_FLASH_AMOUNT, now);
}

void LedController::noteHold(uint8_t key, uint32_t now) {
  startCue(key, Rgb{255, 255, 255}, MK_LED_HOLD_FLASH_MS, MK_LED_HOLD_FLASH_AMOUNT, now);
}

void LedController::noteUnbound(uint8_t key, uint32_t now) {
  // Dim rather than saturated: this is "nothing here", not a fault.
  startCue(key, Rgb{110, 0, 0}, MK_LED_UNBOUND_FLASH_MS, MK_LED_UNBOUND_FLASH_AMOUNT, now);
}

void LedController::noteMacroBusy(uint8_t key, uint32_t now) {
  uint8_t index = key < MK_LED_COUNT ? key : MK_LED_COUNT - 1;
  macroBusy_ = true;
  macroBusyIndex_ = index;
  macroBusyStartedAt_ = now == 0 ? 1 : now;
  dirty_ = true;
}

void LedController::noteMacroDone(uint8_t key, uint32_t now) {
  macroBusy_ = false;
  // Green: same family as a successful profile write on the host path, so
  // "stored / finished" reads as one language on the pad.
  startCue(key, Rgb{0, 255, 60}, MK_LED_MACRO_DONE_MS, MK_LED_MACRO_DONE_AMOUNT, now);
}

void LedController::setHostMode(bool enabled, uint32_t now, uint16_t timeoutMs) {
  hostMode_ = enabled;
  lastHostAt_ = now;
  // Handing control back always restores the default, so one session asking for
  // a long window cannot leave the next host stuck with it.
  hostTimeoutMs_ = (enabled && timeoutMs > 0) ? timeoutMs : MK_LED_HOST_TIMEOUT_MS;
  dirty_ = true;
}

void LedController::setPixel(uint8_t index, Rgb color, uint8_t effect, uint16_t period,
                             uint32_t now) {
  if (index >= MK_LED_COUNT) return;
  ambient_[index] = AmbientPixel{color, now, period, effect};
  noteHostAlive(now);
  dirty_ = true;
}

void LedController::setAll(Rgb color, uint8_t effect, uint16_t period, uint32_t now) {
  for (uint8_t i = 0; i < MK_LED_COUNT; i++) {
    ambient_[i] = AmbientPixel{color, now, period, effect};
  }
  noteHostAlive(now);
  dirty_ = true;
}

void LedController::setFrame(const Rgb *colors, uint8_t count, uint32_t now) {
  for (uint8_t i = 0; i < MK_LED_COUNT; i++) {
    Rgb color = i < count ? colors[i] : Rgb{0, 0, 0};
    ambient_[i] = AmbientPixel{color, now, 0, FX_SOLID};
  }
  noteHostAlive(now);
  dirty_ = true;
}

void LedController::setBar(uint8_t percent, Rgb color, uint32_t now) {
  if (percent > 100) percent = 100;
  // Each pixel is a whole step; the leading pixel dims to show the remainder so
  // a 12% bar is still visibly different from 0%.
  uint16_t filledX256 = (uint16_t)percent * MK_LED_COUNT * 256 / 100;
  for (uint8_t i = 0; i < MK_LED_COUNT; i++) {
    uint16_t low = (uint16_t)i * 256;
    Rgb pixel = {0, 0, 0};
    if (filledX256 >= low + 256) {
      pixel = color;
    } else if (filledX256 > low) {
      pixel = blendToward(Rgb{0, 0, 0}, color, (uint8_t)(filledX256 - low));
    }
    ambient_[i] = AmbientPixel{pixel, now, 0, FX_SOLID};
  }
  noteHostAlive(now);
  dirty_ = true;
}

void LedController::applyPowerLimit(Rgb *frame) const {
  uint32_t channels = 0;
  for (uint8_t i = 0; i < MK_LED_COUNT; i++) {
    channels += (uint32_t)frame[i].r + frame[i].g + frame[i].b;
  }
  uint32_t milliamps = channels * MILLIAMPS_PER_CHANNEL_X255 / 255 +
                       (uint32_t)MK_LED_COUNT * IDLE_MILLIAMPS_PER_PIXEL;
  if (milliamps <= MK_LED_MAX_MILLIAMPS) return;

  uint32_t budget = MK_LED_MAX_MILLIAMPS - (uint32_t)MK_LED_COUNT * IDLE_MILLIAMPS_PER_PIXEL;
  uint16_t factor = (uint16_t)(budget * 256 / (milliamps - (uint32_t)MK_LED_COUNT *
                                                                IDLE_MILLIAMPS_PER_PIXEL));
  for (uint8_t i = 0; i < MK_LED_COUNT; i++) {
    frame[i].r = (uint8_t)(((uint32_t)frame[i].r * factor) >> 8);
    frame[i].g = (uint8_t)(((uint32_t)frame[i].g * factor) >> 8);
    frame[i].b = (uint8_t)(((uint32_t)frame[i].b * factor) >> 8);
  }
}

void LedController::render(uint32_t now) {
  Rgb frame[MK_LED_COUNT];

  for (uint8_t i = 0; i < MK_LED_COUNT; i++) {
    Rgb scene;
    uint8_t effect = FX_SOLID;
    if (hostMode_) {
      scene = ambient_[i].color;
      effect = ambient_[i].effect;
    } else {
      scene = profile_->paletteColor(i);
    }

    // The fade triggers on the scene, not on the rendered colour. An animated
    // effect produces a different colour every frame, so comparing output would
    // restart the fade continuously and freeze the animation on its first step.
    // This also covers a host/local handover, because both
    // change which scene the pixel is reading from.
    if (scene.r != lastScene_[i].r || scene.g != lastScene_[i].g ||
        scene.b != lastScene_[i].b || effect != lastEffect_[i] ||
        hostMode_ != lastHostMode_) {
      fadeFrom_[i] = lastBase_[i];
      fadeStartedAt_[i] = now;
      lastScene_[i] = scene;
      lastEffect_[i] = effect;
    }

    Rgb color = scene;
    if (effect < FX_COUNT) {
      MK_LED_EFFECTS[effect].fn(&color, now - ambient_[i].startedAt, ambient_[i].period, i);
    }

    uint32_t sinceFade = now - fadeStartedAt_[i];
    if (MK_LED_FADE_MS > 0 && sinceFade < MK_LED_FADE_MS) {
      color = blendToward(fadeFrom_[i], color, (uint8_t)(sinceFade * 255 / MK_LED_FADE_MS));
    }
    // Recorded before the press flash: the flash is transient feedback, not the
    // scene, so a fade starting mid-flash must not inherit the white.
    lastBase_[i] = color;

    // Macro-in-progress sits above the scene and below the done flash. Cyan so
    // it cannot be confused with recording red or the white tap cue.
    if (macroBusy_ && i == macroBusyIndex_) {
      uint16_t period = MK_LED_MACRO_BUSY_PERIOD_MS;
      if (period == 0) period = 1;
      uint32_t phase = (now - macroBusyStartedAt_) % period;
      // Triangle 0..255..0 for a soft pulse without a sine table.
      uint16_t wave = phase < period / 2
                          ? (uint16_t)(phase * 510 / period)
                          : (uint16_t)((period - phase) * 510 / period);
      uint8_t amount = (uint8_t)((wave * MK_LED_MACRO_BUSY_AMOUNT) / 255);
      color = blendToward(color, Rgb{0, 180, 255}, amount);
    }

    const Cue &cue = cue_[i];
    uint32_t sinceCue = now - cue.startedAt;
    if (cue.startedAt != 0 && sinceCue < cue.durationMs) {
      uint16_t amount = (uint16_t)(255 - sinceCue * 255 / cue.durationMs);
      amount = amount * cue.amount / 255;
      color = blendToward(color, cue.color, (uint8_t)amount);
    }

    color.r = (uint8_t)(((uint16_t)color.r * brightness_) >> 8);
    color.g = (uint8_t)(((uint16_t)color.g * brightness_) >> 8);
    color.b = (uint8_t)(((uint16_t)color.b * brightness_) >> 8);
    frame[i] = color;
  }

  lastHostMode_ = hostMode_;
  applyPowerLimit(frame);

  bool changed = false;
  for (uint8_t i = 0; i < MK_LED_COUNT; i++) {
    if (frame[i].r != lastShown_[i].r || frame[i].g != lastShown_[i].g ||
        frame[i].b != lastShown_[i].b) {
      changed = true;
      break;
    }
  }
  // Every show() blocks interrupts for ~240 us on this MCU, which USB does not
  // enjoy. Skipping identical frames keeps that cost off the idle path.
  if (!changed) return;

  for (uint8_t i = 0; i < MK_LED_COUNT; i++) {
    strip_.setPixelColor(i, frame[i].r, frame[i].g, frame[i].b);
    lastShown_[i] = frame[i];
  }
  strip_.show();
}

void LedController::update(uint32_t now) {
  if (hostMode_ && now - lastHostAt_ > hostTimeoutMs_) {
    // The desktop app went away. Fall back to the local scene instead of
    // freezing on whatever colour it last sent.
    hostMode_ = false;
    dirty_ = true;
  }
  if (now - lastRenderAt_ < RENDER_INTERVAL_MS) return;
  lastRenderAt_ = now;
  render(now);
  dirty_ = false;
}
