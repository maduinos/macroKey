> 만든 사람: maduinos<br>
> 문서 만든 날짜: 2026-09-02<br>
> https://maduinos.blogspot.com/

# ProMicro RP2040 (16 MB)

`id`: `promicro-rp2040` · 헤더: `firmware/src/boards/promicro_rp2040.h` · FQBN:
`rp2040:rp2040:generic:flash=16777216_14680064,freq=133`

32u4 보드와 외형은 닮았지만 핀 번호도, 저장소도 다릅니다. 매크로 용량이 **70배**입니다.

> 그림 배선도는 [온라인 배선 가이드](https://maduinos.github.io/macroKey/boards/wiring-promicro-rp2040.html)에 있습니다.

## 핀 맵

| 기능 | 핀 | 비고 |
| --- | --- | --- |
| 버튼 1~8 | **GP2 ~ GP9** | `INPUT_PULLUP`, active-low |
| WS2812B DIN | **GP21** | 330 Ω 직렬 저항 경유 |
| WS2812B VDD | **3V3** | **5 V·RAW 아님** — 아래 참고 |
| WS2812B GND | GND | 버튼 GND와 공통 |

> **규격상 WS2812B는 5 V 부품입니다**(V<sub>DD</sub> 3.5~5.3 V). **그런데 이 보드에서는
> 3.3 V로도 동작합니다 — 직접 확인했습니다.** 규격 밖이라 보증되지는 않고, 5 V 때보다
> 어둡고 파랑이 약한 모듈이 있을 수 있습니다.
>
> 그럼에도 이 보드에서는 3.3 V로 물리는 편이 낫습니다. RP2040은 3.3 V 로직이라 데이터도
> 3.3 V로 나가는데, WS2812B의 입력 하이 임계가 0.7 × V<sub>DD</sub>이기 때문입니다.
> 3.3 V 급전이면 임계가 2.31 V로 내려가 여유가 충분합니다. 모듈만 5 V(`RAW`)에 물리면
> 임계가 3.50 V로 올라가 3.3 V 신호로는 미달이고, **되다 안 되다 하는 가장 나쁜 형태**로
> 나타납니다. 판별법과 대안은
> [배선 가이드 07절](https://maduinos.github.io/macroKey/boards/wiring-promicro-rp2040.html#ext)에
> 있습니다.

> **보드에 달린 LED가 아닙니다.** macroKey가 쓰는 것은 GP21에 외부로 연결하는 모듈입니다.
> 온보드 LED는 보드마다 핀도 종류도 다르고(제네릭 클론은 GP17의 비-WS2812 RGB,
> SparkFun 판은 GP25의 WS2812), 펌웨어는 어느 쪽도 건드리지 않습니다.

버튼이 아직 배선되지 않았다면 GPIO를 GND에 직접 대서 입력을 시험할 수 있습니다(키 0~7 =
GP2~GP9, active-low).

## 저장소

RP2040에는 EEPROM이 없습니다. 프로필은 RAM 이미지이고, `begin()`이 LittleFS에서 읽어오고
`commit()`이 원자적 rename으로 되씁니다. **둘 중 하나라도 빠지면 패드가 쓰레기를 읽거나,
뽑는 순간 다 잊습니다 — 그리고 어디에서도 실패하지 않습니다.**

| | |
| --- | --- |
| 프로필 | 65,520 B, 스키마 3 |
| 매크로 영역 | 21,801 레코드 |
| 슬롯당 최대 | 21,800 레코드 — 카운트가 **2바이트**라 영역 크기가 한계 |

스키마 3은 32u4의 스키마 2와 **호환되지 않습니다.** 크기도 카운트 폭도 다른 별개의 이미지라,
패드는 다른 스키마의 바이트를 해석하는 대신 거부하고 기본값을 씁니다.

## 첫 플래싱: BOOTSEL

**BOOTSEL 버튼을 누른 채로 USB를 꽂습니다.** `RPI-RP2`라는 드라이브가 뜹니다. 앱이 그 드라이브가
나타나는 것을 지켜보다가 즉시 `.uf2`를 복사합니다.

```bash
macrokey flash
```

**두 번째부터는 BOOTSEL이 필요 없습니다.** 코어가 1200 bps 터치로 자동 진입하고, 펌웨어에도
`BOOT` 명령이 살아 있습니다(`reset_usb_boot`). BOOTSEL은 첫 플래싱과 폴백용입니다.

## 이 보드에서 겪는 함정

**USB 허브에 물리지 마세요.** Genesys 허브(`05e3:0608`)에 꽂았을 때 첫 열거부터
`error -32`/`-71`로 실패하다가 커널 USB 스택이 통째로 물렸습니다 —
`__usb_queue_reset_device`가 D 상태로 걸리고, 재부팅 전까지 그 버스의 hotplug가 죽습니다.
**메인보드 직결 포트에 1 m 이하 데이터 케이블**로 꽂으면 한 번에 붙습니다.

**`/dev/ttyACM*` 번호를 고정으로 믿지 마세요.** 이 보드만 ACM으로 잡히는 게 아닙니다 — 일부
모니터(예: LG `043e:9a8a`)도 ACM으로 열거되어, 패드가 재열거될 때마다 번호가 뒤바뀝니다.
번호로 열면 조용히 엉뚱한 장치를 듣게 됩니다. `macrokey ports`나 시리얼 번호로 찾으세요.
앱은 USB id로 찾으므로 영향받지 않습니다.

**부트로더 드라이브를 마운트해 줄 무언가가 필요합니다.** 이 보드의 플래싱은 파일 복사이고,
복사하려면 `RPI-RP2` 볼륨이 먼저 마운트돼 있어야 합니다. 데스크톱 세션에서는 udisks가
자동으로 해주므로 아무 일도 일어나지 않은 것처럼 그냥 됩니다 — 하지만 자동 마운트가 없는
환경(헤드리스 서버, 최소 컨테이너, 자동 마운트를 끈 데스크톱)에서는 앱이 볼륨을 기다리다
`BootloaderTimeout`으로 끝납니다. 보드도 케이블도 멀쩡한데 실패하므로 원인이 잘 안 보입니다.
직접 마운트한 뒤 그 경로로 복사하면 됩니다.

Pro Micro(AVR109)에는 해당되지 않습니다. 그쪽은 시리얼 포트로 직접 쓰기 때문에 파일시스템이
끼어들지 않습니다. **펌웨어 자동 업데이트도 이 경로를 그대로 씁니다** — 무인으로 도는 만큼,
자동 마운트가 없는 기계에서는 업데이트가 조용히 타임아웃으로 끝난다는 뜻입니다.

USB id는 스케치가 어떤 인터페이스를 켜느냐에 따라 달라집니다: CDC 전용 `2e8a:000a`,
CDC+HID 복합 `2e8a:f009`/`f00a`/`f10a`. 부트로더는 `2e8a:0003`이고 시리얼 포트가 아니라
**대용량 저장 장치**로 뜹니다 — 그래서 포트 목록에는 나타나지 않고, 볼륨으로 찾습니다.

## 소스에서 빌드 (개발용)

```bash
arduino-cli config add board_manager.additional_urls \
  https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json
arduino-cli core update-index
arduino-cli core install rp2040:rp2040
arduino-cli lib install "Adafruit NeoPixel"

arduino-cli compile \
  --fqbn rp2040:rp2040:generic:flash=16777216_14680064,freq=133 firmware
```

`flash=16777216_14680064`는 16 MB 중 14 MB를 LittleFS에 주는 설정입니다. 프로필이 사는
파일 시스템이라 이 값을 줄이면 저장소가 함께 줄어듭니다.
