# macroKey 하드웨어

Arduino Leonardo(ATmega32u4) 기준입니다. Micro / Pro Micro도 핀 이름이 같아 그대로 씁니다.

## 핀 맵

`arduino/macrokey/src/Config.h`의 `MK_KEY_PINS`와 `MK_LED_PIN`이 유일한 진실 공급원입니다.
아래 표는 기본값입니다.

| 기능 | 핀 | 비고 |
| --- | --- | --- |
| 버튼 1~8 | D3, D4, D5, D6, D7, D8, D9, D10 | `INPUT_PULLUP`, active-low. 기존 배선 그대로 |
| WS2812 DIN | **D11** | 신규. I2C/SPI/Serial과 겹치지 않는 자유 핀 |
| WS2812 5V | 5V | |
| WS2812 GND | GND | 버튼 GND와 공통 |

버튼은 한쪽을 핀에, 반대쪽을 GND에 연결합니다. 내부 풀업을 쓰므로 외부 저항은 필요 없습니다.

> **D2를 쓰지 마세요.** D2/D3은 각각 SDA/SCL입니다. D3은 버튼으로 이미 쓰고 있으므로
> I2C를 나중에 붙일 계획이면 버튼을 D2~D9로 한 칸 옮기고 WS2812를 D10으로 보내세요.
> `Config.h` 한 곳만 고치면 됩니다.

WS2812 DIN 라인에는 **330~470 Ω 직렬 저항**을, 5V–GND 사이에는 첫 픽셀 가까이에
**1000 µF 전해 커패시터**를 답니다. 없어도 대개 동작하지만 첫 픽셀이 튀거나 색이
잘못 나오는 원인의 대부분이 이 두 가지입니다.

## 전력 예산

USB 2.0 장치는 500 mA를 넘게 쓸 수 없습니다. WS2812 하나는 흰색 최대에서 채널당 약
20 mA, 즉 **픽셀당 60 mA**를 씁니다.

| 조건 | 전류 | 판정 |
| --- | --- | --- |
| 8픽셀 흰색 100% | 480 mA | ATmega32u4 자체 소비까지 더하면 예산 초과 |
| 8픽셀 흰색 25% | 120 mA | 안전 |
| 8픽셀 단색 25% | 40 mA | 넉넉 |

그래서 `LedController`는 두 겹으로 방어합니다.

1. 기본 전역 밝기 **64/255 (25%)**
2. 프레임 송출 직전 전 채널 합으로 예상 전류를 계산하고 **400 mA 상한**으로 선형 스케일링

상한은 `Config.h`의 `MK_LED_MAX_MILLIAMPS`입니다. 외부 5V 전원을 따로 넣는다면 올려도
됩니다. 그럴 때 **Arduino의 5V 핀과 외부 전원을 동시에 연결하지 마세요.** GND만 공통으로
묶고 DIN을 연결합니다.

## WS2812와 USB 타이밍

WS2812는 비트뱅 프로토콜이라 전송 중 인터럽트를 막습니다. ATmega32u4에서 8픽셀 갱신은
약 240 µs 동안 인터럽트를 차단하고, USB SOF 주기는 1 ms입니다. 여유는 있지만 무제한은
아닙니다. 펌웨어는 이렇게 대응합니다.

- LED 갱신을 **최대 50 Hz로 제한** (`MK_LED_FPS`)
- 프레임 버퍼가 **실제로 바뀌었을 때만** 송출
- 키 스캔 직후가 아니라 루프 끝에서 송출해 입력 지연에 영향을 주지 않음

USB 열거가 불안정하거나 키 입력이 씹히면 가장 먼저 `MK_LED_FPS`를 30으로 낮춰 보세요.

## 부팅 안전장치

펌웨어는 부팅 후 `MK_BOOT_GRACE_MS`(기본 2000 ms) 동안 HID 입력을 보내지 않습니다.
매크로가 잘못 설정돼 무한히 키를 뿜는 상태가 되면 이 2초가 재업로드할 유일한 기회입니다.

그마저 놓쳤다면 Leonardo의 리셋 버튼을 **빠르게 두 번** 눌러 부트로더를 8초간 붙잡은 뒤
업로드하세요.

## 빌드

```bash
arduino-cli core install arduino:avr
arduino-cli lib install "Adafruit NeoPixel"
arduino-cli compile --fqbn arduino:avr:leonardo arduino/macrokey
arduino-cli upload  --fqbn arduino:avr:leonardo -p /dev/ttyACM0 arduino/macrokey
```

`Keyboard`와 `Mouse`는 AVR 코어에 포함돼 있어 따로 설치하지 않습니다.
