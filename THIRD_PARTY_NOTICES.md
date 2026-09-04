> 만든 사람: maduinos<br>
> 문서 만든 날짜: 2026-09-04<br>
> https://maduinos.blogspot.com/

# 제3자 구성요소 고지

macroKey가 직접 쓴 코드는 [MIT License](LICENSE)입니다. 그런데 릴리스에 올라가는
실행 파일과 펌웨어 이미지에는 다른 사람의 코드가 함께 들어갑니다. 그 목록과 조건이
이 문서입니다.

**소스만 받아 쓰는 경우에는 이 문서가 필요하지 않습니다.** 저장소의 코드 자체는
전부 MIT입니다. 아래 의무는 완성된 바이너리 — `macrokey-linux-x86_64`,
`macrokey-windows-x86_64.exe`, `firmware-*.hex`, `firmware-*.uf2` — 를 배포할 때
생깁니다.

## 설정 앱 바이너리

`build_release.sh`가 PyInstaller로 묶는 단일 실행 파일에 들어가는 것들입니다.

| 구성요소 | 버전 | 라이선스 |
| --- | --- | --- |
| Qt 6 / PySide6 / shiboken6 | 6.11.2 | **LGPL-3.0** |
| pynput | 1.8.2 | **LGPL-3.0** |
| python-xlib (X11에서 pynput이 씀) | 0.33 | **LGPL-2.1 이상** |
| pyserial | 3.5 | BSD-3-Clause |
| python-evdev (Linux) | 1.9.3 | BSD-3-Clause |
| six | 1.17.0 | MIT |
| CPython | 3.10 | PSF License |

PySide6는 LGPL-3.0 / GPL-2.0 / GPL-3.0 셋 중 고를 수 있게 배포됩니다. macroKey는
**LGPL-3.0으로 씁니다.** LGPL은 GPL과 달리 이 앱의 코드까지 같은 라이선스로 만들 것을
요구하지 않으므로 macroKey 코드는 MIT로 남습니다.

## 펌웨어 이미지

| 구성요소 | 버전 | 라이선스 | 쓰는 보드 |
| --- | --- | --- | --- |
| Adafruit_NeoPixel | 1.15.4 | **LGPL-3.0** | 둘 다 |
| Arduino AVR Boards 코어 (`EEPROM` 포함) | 1.8.8 | **LGPL-2.1 이상** | Pro Micro |
| Arduino `Keyboard` | 1.0.6 | **LGPL-2.1 이상** | Pro Micro |
| Arduino `Mouse` | 1.0.1 | **LGPL-2.1 이상** | Pro Micro |
| SparkFun AVR Boards 보드 정의 | 1.1.13 | **LGPL-2.1 이상** | Pro Micro |
| arduino-pico 코어 (`Keyboard`·`Mouse`·`LittleFS` 포함) | 6.0.0 | **LGPL-2.1 이상** | RP2040 |
| littlefs | arduino-pico 동봉 | BSD-3-Clause | RP2040 |
| Raspberry Pi Pico SDK | arduino-pico 동봉 | BSD-3-Clause | RP2040 |

`firmware/src/HidBackend.h`가 참조하는 **HID-Project(NicoHood)는 배포 이미지에 들어
있지 않습니다.** `Config.h`의 `MK_USE_HID_PROJECT`가 `0`이라 그 경로는 컴파일되지
않습니다. 이 값을 `1`로 바꿔 직접 빌드하면 HID-Project(MIT)가 추가로 포함됩니다.

## LGPL 준수 방법

LGPL 구성요소를 포함해 바이너리를 배포할 때 해야 하는 것은 둘입니다.

**1. 고지.** 이 문서와 각 라이선스 전문을 배포물과 함께 제공합니다. GitHub 릴리스
페이지에서 이 저장소를 링크하는 것으로 충족합니다.

**2. 교체 가능성.** 사용자가 LGPL 라이브러리를 자기가 고친 버전으로 바꿔 넣을 수
있어야 합니다.

- **설정 앱**은 이미 충족합니다. PyInstaller onefile은 Qt를 실행 시점에 별도
  `.so`/`.dll`로 풀어놓는 동적 링크라, 사용자가 그 파일을 교체하거나
  `pip install PySide6` 후 소스에서 `python main.py`로 실행하면 다른 Qt로 돌릴 수
  있습니다.
- **펌웨어**는 정적 링크입니다. `.hex`/`.uf2` 안에서 LGPL 코드와 macroKey 코드가
  한 덩어리로 섞여 있어 파일 교체로는 바꿀 수 없습니다. 대신 **누구나 같은 이미지를
  재현할 수 있게** 해서 충족합니다 — 펌웨어 소스 전체가 이 저장소에 MIT로 있고,
  `tools/build_firmware.sh`가 쓰는 코어와 라이브러리 버전이 `macrokey/boards.py`에
  고정되어 있습니다.

  ```bash
  ./tools/build_firmware.sh                  # 등록된 모든 보드
  ./tools/build_firmware.sh promicro-rp2040  # 하나만
  ```

## 빌드 도구 (배포물에 포함되지 않음)

| 도구 | 버전 | 라이선스 |
| --- | --- | --- |
| PyInstaller | 6.22.2 | GPL-2.0 이상 + 예외조항 |
| pyinstaller-hooks-contrib | 2026.7 | Apache-2.0 |
| pytest, ruff | — | MIT |
| arduino-cli | — | GPL-3.0 |

PyInstaller의 GPL에는 **"PyInstaller로 만든 프로그램은 자유 소프트웨어가 아니어도
된다"는 명시적 예외**가 붙어 있습니다. 그래서 macroKey 바이너리가 GPL이 되지
않습니다. arduino-cli도 마찬가지로 컴파일러를 부르는 도구일 뿐, 결과물인 펌웨어에
들어가지 않습니다.

## 정정 요청

빠졌거나 잘못 적힌 항목이 있으면 [이슈](https://github.com/maduinos/macroKey/issues)로
알려 주시면 확인 후 바로 고칩니다.
