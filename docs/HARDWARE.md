# macroKey 하드웨어

Pro Micro(ATmega32u4, **5 V / 16 MHz 버전**) 기준입니다. 전원은 PC USB 하나뿐입니다.

> 그림으로 된 배선 다이어그램(핀 맵, 버튼 1개 상세, WS2812B 결선, 전체 결선도, 전력 예산)은
> [`wiring.html`](wiring.html)에 있습니다. 브라우저로 열거나 그대로 인쇄해서 작업대에 두고 쓰세요.
> 이 문서는 같은 내용의 텍스트 요약입니다.

## 핀 맵

`firmware/src/Config.h`의 `MK_KEY_PINS`와 `MK_LED_PIN`이 유일한 진실 공급원입니다.
아래 표는 기본값입니다.

| 기능 | 핀 | 위치 | 비고 |
| --- | --- | --- | --- |
| 버튼 1~7 | D3 ~ D9 | 아래쪽 줄 | `INPUT_PULLUP`, active-low |
| 버튼 8 | D10 | **위쪽 줄 맨 끝** | 혼자만 반대쪽 줄에 있음 |
| WS2812B DIN | **A0** (디지털 18) | 위쪽 줄 | 330 Ω 직렬 저항 경유 |
| WS2812B 5V | **VCC** | 위쪽 줄 | `RAW` 아님 |
| WS2812B GND | GND | 위/아래 줄 | 버튼 GND와 공통 |

버튼은 한쪽을 핀에, 반대쪽을 GND에 연결합니다. 내부 풀업을 쓰므로 외부 저항은 필요 없습니다.

> **Pro Micro에는 D11이 없습니다.** ATmega32u4 칩에는 D11·D12·D13이 있지만 Pro Micro 보드는
> 그 핀을 밖으로 빼지 않습니다. Leonardo용 자료를 그대로 따라 하면 존재하지 않는 핀에 배선하게
> 됩니다. 노출된 핀 중 D2/D3은 SDA/SCL, D14/D15/D16은 SPI라서, 어느 버스와도 겹치지 않는
> **A0**를 LED 데이터 핀으로 잡았습니다. A0는 VCC·GND와 같은 쪽 헤더에 있어 배선이 한쪽으로
> 정리됩니다.

> **VCC와 RAW를 구분하세요.** `RAW`는 USB VBUS가 그대로 나오는 핀, `VCC`는 레귤레이터를 거친
> 5 V 출력입니다. WS2812B는 `VCC`에 물립니다. 그리고 반드시 **5 V / 16 MHz 버전** 보드여야
> 합니다. 3.3 V / 8 MHz 버전은 VCC가 3.3 V라서 WS2812B가 제대로 켜지지 않습니다.

WS2812B DIN 라인에는 **330~470 Ω 직렬 저항**을, VCC–GND 사이에는 모듈 가까이에
**100 µF 전해 커패시터**를 답니다.

> **1000 µF를 쓰지 마세요.** 흔히 보이는 그 값은 긴 스트립에 전용 어댑터를 다는 경우의
> 권장값입니다. 이 프로젝트는 픽셀 1개를 PC USB로만 급전하고, USB 2.0 규격은 장치의 벌크
> 커패시턴스를 10 µF로 제한합니다. 실제 포트는 수백 µF까지 견디지만 큰 용량은 돌입 전류로
> 포트 보호를 걸어 "USB 장치를 인식할 수 없음"이 되는 원인이 됩니다. 픽셀 1개에는 100 µF면
> 충분합니다.

## 전력 예산

전원은 PC USB 하나뿐이고 USB 2.0 장치는 500 mA를 넘게 쓸 수 없습니다. WS2812B 하나는
흰색 최대에서 채널당 약 20 mA, 즉 **60 mA**를 씁니다.

| 조건 | 전류 | 판정 |
| --- | --- | --- |
| 픽셀 1개 흰색 100% + MCU | 90 mA | 예산의 18%. 안전 |
| 픽셀 1개 단색 25% + MCU (기본값) | 35 mA | 예산의 7%. 넉넉 |

픽셀이 하나뿐이라 전력은 사실상 걱정거리가 아닙니다. 밝기를 100%로 올려도 안전합니다.
`Config.h`의 `MK_LED_MAX_MILLIAMPS`는 100으로 잡혀 있지만 1픽셀이 60 mA를 넘길 수 없어
실제로는 걸리지 않습니다. 그래도 남겨 둔 이유는 나중에 `MK_LED_COUNT`를 올리는 순간
다른 곳을 고치지 않아도 이 한 줄이 바로 안전장치로 동작하기 때문입니다.

USB 급전이라서 신경 쓸 것이 하나 있습니다. **케이블이 조용한 원인입니다.** 길고 얇은 충전 전용
케이블(28 AWG, 2 m 이상)은 보드 쪽 전압을 4.6 V 근처까지 끌어내려 LED 색이 틀어지거나 32u4가
브라운아웃으로 리셋되게 만듭니다. 1 m 이하 데이터용 케이블을 쓰세요.

## WS2812B와 USB 타이밍

WS2812B는 비트뱅 프로토콜이라 전송 중 인터럽트를 막습니다. 픽셀 하나 갱신은 약 30 µs이고
USB SOF 주기는 1 ms이므로 여유가 충분합니다. 8픽셀 스트립일 때 걱정하던 USB 열거 불안정
문제는 픽셀 1개에서는 발생하지 않습니다. `MK_LED_FPS`(기본 50 Hz)는 그대로 두면 됩니다.

## 픽셀 1개로 무엇을 보여주는가

LED 합성 우선순위(높은 것이 이김):

1. 키/매크로 큐 — 탭 흰색, 빈 슬롯 어두운 빨강, 매크로 완료 초록
2. 매크로 실행 중 — 시안 펄스
3. 호스트 오버레이 — 설정 앱이 녹음 중 칠하는 빨강/분홍 등
4. 기본 씬 — 프로필 바탕색

8픽셀 설계에서 두 가지가 줄어듭니다.

- **키별 위치 표시가 불가능합니다.** 펌웨어는 모든 키의 누름을 이 하나의 픽셀로 모읍니다.
- AgentPet 상시 상태 LED는 없습니다(앱이 상시 실행되지 않음).

스트립으로 바꾸고 싶어지면 `Config.h`의 `MK_LED_COUNT`와 호스트
`config/model.py`의 `LED_COUNT`를 같은 값으로 올리면 됩니다. EEPROM 팔레트 크기와 전류
제한은 그 값에서 자동으로 따라옵니다.

## 부팅 안전장치

펌웨어는 부팅 후 `MK_BOOT_GRACE_MS`(기본 2000 ms) 동안 HID 입력을 보내지 않습니다.
매크로가 잘못 설정돼 무한히 키를 뿜는 상태가 되면 이 2초가 재업로드할 유일한 기회입니다.

> **Pro Micro에는 리셋 버튼이 없습니다.** 부트로더로 들어가려면 `RST` 핀과 `GND`를 점퍼선이나
> 핀셋으로 **빠르게 두 번 단락**시켜야 합니다. 그러면 8초간 부트로더가 뜨고, 그 안에 업로드를
> 시작해야 합니다. **배선할 때 RST와 GND에 닿을 수 있는 점퍼선을 미리 하나 꽂아 두세요.**
> 잘못된 펌웨어에서 빠져나오는 유일한 길입니다.

## 빌드

Pro Micro는 `arduino:avr` 코어에 들어 있지 않습니다. SparkFun 보드 패키지를 먼저 추가합니다.

```bash
arduino-cli config add board_manager.additional_urls \
  https://raw.githubusercontent.com/sparkfun/Arduino_Boards/master/IDE_Board_Manager/package_sparkfun_index.json
arduino-cli core update-index
arduino-cli core install SparkFun:avr
arduino-cli lib install "Adafruit NeoPixel"
arduino-cli lib install "Keyboard"
arduino-cli lib install "Mouse"

arduino-cli compile --upload --fqbn SparkFun:avr:promicro:cpu=16MHzatmega32U4 \
  -p /dev/ttyACM0 firmware
```

`compile`만 하고 `upload`만 하면 예전 빌드가 올라갈 수 있습니다. 항상 `compile --upload`로
한 번에 올리세요.

`Keyboard`와 `Mouse`는 AVR 코어에 **들어 있지 않으므로** 위처럼 각각 설치해야 합니다.
빠뜨리면 `HidBackend.h`가
`Mouse.h`를 찾지 못하고 컴파일이 멈춥니다.

업로드는 타이밍 싸움입니다. RST–GND를 빠르게 두 번 단락시킨 **직후에** upload를 실행하세요.
부트로더 모드에서는 포트 번호가 바뀌는 경우가 많으니 `arduino-cli board list` 또는 `python -m macrokey ports`로 확인하세요.
