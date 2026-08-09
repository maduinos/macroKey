// macroKey firmware -- Arduino Leonardo / Micro / Pro Micro.
//
// Eight GPIO buttons and an eight pixel WS2812 strip presented as a USB HID
// keyboard and mouse. The pad is fully usable with no software installed; the
// desktop app adds recording, long macros and AgentPet-driven lighting.
//
// docs/ARCHITECTURE.md  design and rationale
// docs/PROTOCOL.md      serial wire format
// docs/HARDWARE.md      pinout and power budget

#include "src/ButtonInput.h"
#include "src/Config.h"
#include "src/KeyEngine.h"
#include "src/LedController.h"
#include "src/Profile.h"
#include "src/SerialProtocol.h"

static Profile gProfile;
static ButtonInput gInput;
static LedController gLeds;
static KeyEngine gEngine;
static SerialProtocol gSerial;

static uint32_t gLastScanAt = 0;

// RX and TX sit on PB0 and PD5, wired from VCC through a resistor down to the
// pin, so the pin lights them by sinking current. USB_Send() turns them on for
// 100 ms on every transfer -- HID reports included, which is why a keypress
// blinks them -- and it does so from the USB interrupt, where nothing can be
// hooked. Driving them off again just races that ISR and still leaves a flicker.
//
// Making the pins inputs with the pull-up off removes the sink entirely: there
// is no path for current, so the core can write the port all it likes and the
// LEDs cannot light. It has to run every pass rather than once in setup()
// because a USB reset re-runs TX_RX_LED_INIT and makes them outputs again.
#if MK_QUIET_BOARD_LEDS && defined(RXLED0) && defined(TXLED0)
static inline void quietBoardLeds() {
  DDRB &= (uint8_t) ~(1 << 0);
  PORTB &= (uint8_t) ~(1 << 0);
  DDRD &= (uint8_t) ~(1 << 5);
  PORTD &= (uint8_t) ~(1 << 5);
}
#else
static inline void quietBoardLeds() {}
#endif

// KeyEngine reports through plain function pointers so it carries no dependency
// on the serial layer. These trampolines are the only place the two meet.
static void reportKey(uint8_t key, uint8_t gesture, uint8_t layer, bool released) {
  gSerial.sendKeyEvent(key, gesture, layer, released);
}

static void reportHost(uint8_t token, uint8_t key, uint8_t layer) {
  gSerial.sendHostAction(token, key, layer);
}

static void reportRecordRequest(uint8_t key) {
  gSerial.sendRecordRequest(key);
}

// Called from inside a replaying macro. A macro runs in loop(), so without this
// the pixel holds whatever it was showing for the whole replay -- and replaying
// the pauses is the point, so that can be seconds of a pad that looks dead.
//
// LEDs and the board LEDs only. Serial is deliberately left queued: the host
// writes profiles, and committing one while a macro is reading its records out
// of EEPROM would change the steps out from under it.
static void macroYield() {
  quietBoardLeds();
  gLeds.update(millis());
}

void setup() {
  quietBoardLeds();
  gInput.begin();

  bool profileValid = gProfile.begin();
  gLeds.begin(&gProfile);
  gEngine.begin(&gProfile, &gInput, &gLeds);
  gEngine.setReportCallbacks(reportKey, reportHost);
  gEngine.setRecordCallback(reportRecordRequest);
  gEngine.setMacroYield(macroYield);
  gSerial.begin(&gProfile, &gEngine, &gLeds);

  // Announce ourselves unconditionally: a host that opens the port later will
  // send IDENT, and one that was already listening gets the greeting for free.
  gSerial.sendHello();
  if (!profileValid) gSerial.sendLog('w', "profile reset to defaults");
}

void loop() {
  uint32_t now = millis();

  quietBoardLeds();

  if (now - gLastScanAt >= MK_SCAN_INTERVAL_MS) {
    gLastScanAt = now;
    gInput.update(now);
    gEngine.update(now);
  }

  gSerial.update(now);

  // LEDs go last. The WS2812 bit-bang blocks interrupts, so it stays off the
  // path between a key press and its HID report.
  gLeds.update(now);
}
