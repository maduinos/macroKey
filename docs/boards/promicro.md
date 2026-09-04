> 만든 사람: maduinos<br>
> 문서 만든 날짜: 2026-09-02<br>
> https://maduinos.blogspot.com/

# Pro Micro (ATmega32u4)

`id`: `promicro` · 헤더: `firmware/src/boards/promicro_32u4.h` · FQBN:
`SparkFun:avr:promicro:cpu=16MHzatmega32U4`

macroKey의 원래 하드웨어입니다. **5 V / 16 MHz 버전**이어야 합니다 — 펌웨어가
`cpu=16MHzatmega32U4`로 빌드되므로 8 MHz 보드에 구우면 클럭이 어긋나 USB가 열거되지
않습니다. 이게 넘을 수 없는 쪽이고, LED는 그다음 문제입니다: 이 보드는 로직이 5 V라
WS2812B도 `VCC`(5 V)에서 급전해 전압을 맞춥니다.

> 그림 배선도(핀 맵, 버튼 1개 상세, WS2812B 결선, 전체 결선도, 전력 예산)는
> [온라인 배선 가이드](https://maduinos.github.io/macroKey/boards/wiring-promicro.html)에 있습니다. 인쇄해서 작업대에 두고 쓰세요.

## 핀 맵

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
> 5 V 출력입니다. WS2812B는 `VCC`에 물립니다.

> **원칙은 하나입니다 — LED 급전 전압을 보드 로직 전압에 맞춥니다.** 이 보드는 둘 다 5 V라
> `VCC`이고, 3.3 V 로직인 RP2040 판은 `3V3`입니다. 섞으면 WS2812B의 입력 하이 임계
> (0.7 × V<sub>DD</sub>)에 걸립니다.

DIN 라인에 **330~470 Ω 직렬 저항**, VCC–GND 사이 모듈 가까이에 **100 µF 전해 커패시터**.
용량 선택 이유는 [HARDWARE.md](../HARDWARE.md#전력-예산)에 있습니다.

## 저장소

1 KB 온칩 EEPROM 전체가 프로필입니다. 메모리 맵드라서 읽기는 첫 명령부터 되고 쓰기는 그
자리에서 남습니다 — `begin()`과 `commit()`이 할 일이 없습니다.

| | |
| --- | --- |
| 프로필 | 1,024 B, 스키마 2 |
| 매크로 영역 | 308 레코드 (레코드당 3 B) |
| 슬롯당 최대 | 255 레코드 — 슬롯 카운트가 **1바이트**라서 |

255는 임의의 숫자가 아니라 카운트 폭입니다. 늘리려면 `MK_MACRO_COUNT_BYTES`를 2로 올려야
하고, 그건 스키마가 바뀌는 일입니다.

## 첫 플래싱: RST–GND 더블탭

> **Pro Micro에는 리셋 버튼이 없습니다.** `RST` 핀과 `GND`를 점퍼선이나 핀셋으로 **빠르게 두 번
> 단락**시키면 8초간 부트로더가 뜹니다. **배선할 때 RST와 GND에 닿을 수 있는 점퍼선을 미리
> 하나 꽂아 두세요.** 잘못된 펌웨어에서 빠져나오는 유일한 길입니다.

앱에서:

```bash
macrokey flash
```

앱이 "RST–GND를 빠르게 두 번 단락시키세요"라고 띄우고 부트로더 포트가 뜨는 것을 지켜보다가,
뜨는 즉시 굽습니다. 예전처럼 더블탭 직후에 명령을 손으로 실행하는 타이밍 싸움이 아닙니다.

부트로더는 8초만 살아 있고, 그 사이 포트 번호가 바뀝니다(`2341:0036`, `1b4f:9205` 등).
앱은 번호가 아니라 USB id로 찾으므로 신경 쓸 필요 없습니다.

**두 번째부터는 자동입니다.** macroKey 펌웨어가 이미 돌고 있으면 앱이 `BOOT` 명령이나 1200 bps
터치로 직접 Caterina에 넣습니다.

## 부팅 안전장치

펌웨어는 부팅 후 `MK_BOOT_GRACE_MS`(2초) 동안 HID를 보내지 않습니다. 매크로가 잘못돼 키를
무한히 뿜는 상태가 되면 이 2초가 재플래싱할 유일한 창입니다. 그래서 위의 점퍼선을 미리 꽂아
두라고 하는 것입니다.

## 소스에서 빌드 (개발용)

Pro Micro는 `arduino:avr` 코어에 없습니다. SparkFun 보드 패키지를 먼저 추가합니다.

```bash
arduino-cli config add board_manager.additional_urls \
  https://raw.githubusercontent.com/sparkfun/Arduino_Boards/master/IDE_Board_Manager/package_sparkfun_index.json
arduino-cli core update-index
arduino-cli core install SparkFun:avr
arduino-cli lib install "Adafruit NeoPixel" "Keyboard" "Mouse"

arduino-cli compile --upload --fqbn SparkFun:avr:promicro:cpu=16MHzatmega32U4 \
  -p /dev/ttyACM0 firmware
```

`Keyboard`와 `Mouse`는 AVR 코어에 **들어 있지 않으므로** 따로 설치해야 합니다. 빠뜨리면
`HidBackend.h`가 `Mouse.h`를 찾지 못하고 컴파일이 멈춥니다.

`compile`과 `upload`를 따로 하면 예전 빌드가 올라갈 수 있습니다. 항상 `compile --upload`로
한 번에 하세요.
