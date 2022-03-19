#include <Keyboard.h>
#define _TEST_

enum FSMState {
    IDLE,
    MACRO_1,
    MACRO_2,
    MACRO_3,
    MACRO_4,
    MACRO_5,
    MACRO_6,
    MACRO_7,
    MACRO_8
};
FSMState state = IDLE;

const int MACRO_1_PIN = 4;
const int MACRO_2_PIN = 5;
const int MACRO_3_PIN = 6;
const int MACRO_4_PIN = 7;
const int MACRO_5_PIN = 8;
const int MACRO_6_PIN = 9;
const int MACRO_7_PIN = 10;
const int MACRO_8_PIN = 11;

unsigned long gPreTime = 0;
unsigned long gCurTime = 0;

void setup() {
    pinMode(MACRO_1_PIN, INPUT_PULLUP);
    pinMode(MACRO_2_PIN, INPUT_PULLUP);
    pinMode(MACRO_3_PIN, INPUT_PULLUP);
    pinMode(MACRO_4_PIN, INPUT_PULLUP);
    pinMode(MACRO_5_PIN, INPUT_PULLUP);
    pinMode(MACRO_6_PIN, INPUT_PULLUP);
    pinMode(MACRO_7_PIN, INPUT_PULLUP);
    pinMode(MACRO_8_PIN, INPUT_PULLUP);
    Keyboard.begin();
}

void loop() {
   #ifdef _TEST_
    gCurTime = millis();
  if( (gCurTime - gPreTime) > 100 ) {
    gPreTime = gCurTime;
    RunKey();
  }
#else
  DelayMS(RunKey, 100);
#endif
}

void DelayMS(int (*fp)(void), int time ) {
    gCurTime = millis();
  if( (gCurTime - gPreTime) > time) {
    gPreTime = gCurTime;
    fp();
  }
}


int RunKey() {
    if (digitalRead(MACRO_1_PIN) == LOW) {
        MacroKeyPressRelease('1');
    }
    else if (digitalRead(MACRO_2_PIN) == LOW) {
        MacroKeyPressRelease('2');
    }
    else if (digitalRead(MACRO_3_PIN) == LOW) {
        MacroKeyPressRelease('3');
    }
    else if (digitalRead(MACRO_4_PIN) == LOW) {
        MacroKeyPressRelease('4');
    }
    else if (digitalRead(MACRO_5_PIN) == LOW) {
        MacroKeyPressRelease('5');
    }
    else if (digitalRead(MACRO_6_PIN) == LOW) {
        MacroKeyPressRelease('6');
    }
    else if (digitalRead(MACRO_7_PIN) == LOW) {
        MacroKeyPressRelease('7');
    }
    else if (digitalRead(MACRO_8_PIN) == LOW) {
        MacroKeyPressRelease('8');
    }
    else {
      Keyboard.releaseAll();
    }

    return 0;
}

int MacroKeyPressRelease(char key) {
    Keyboard.press(KEY_TAB);
    Keyboard.press(key);
    delay(500);
    Keyboard.release(key);
    Keyboard.press(KEY_TAB);
    delay(500);
    //Keyboard.press((char)32); // spacebar

    return 0;
}
