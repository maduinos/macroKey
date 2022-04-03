#include <Keyboard.h>

const int MACRO_1_PIN = 3;
const int MACRO_2_PIN = 4;
const int MACRO_3_PIN = 5;
const int MACRO_4_PIN = 6;
const int MACRO_5_PIN = 7;
const int MACRO_6_PIN = 8;
const int MACRO_7_PIN = 9;
const int MACRO_8_PIN = 10;

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
  DelayMS(RunKey, 100);
}

void DelayMS(int (*fp)(void), int time ) {
  gCurTime = millis();
  if ( (gCurTime - gPreTime) > time) {
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
  delay(100);
  Keyboard.release(key);
  Keyboard.release(KEY_TAB);
  delay(100);
  //Keyboard.press((char)32); // spacebar

  return 0;
}
