# macroKey

Maduinos의 개인 매크로 키패드 프로젝트입니다.

버튼 8개짜리 Pro Micro 키패드를 "고정 단축키 8개"에서 제스처·녹음·LED 상태 표시를
갖춘 매크로 플랫폼으로 확장합니다. 취미/실험 프로젝트이며 Maduinos FPGA 비즈니스 포트폴리오와는
구분합니다.

핵심 원칙은 **device-first, host-optional** 입니다. 펌웨어만 올리면 PC에 아무것도 설치하지 않아도
평범한 USB HID 키보드로 동작하고, 호스트 앱은 긴 텍스트·클립보드 이미지·셸 명령·LED 상태 표시를
얹는 선택적 증폭기입니다.

## 구성

| 경로 | 설명 |
| --- | --- |
| `firmware/` | Pro Micro(ATmega32u4, 5 V / 16 MHz) 펌웨어 |
| `firmware/src/` | `KeyEngine`, `Profile`(EEPROM), `LedController`, `ButtonInput`, `SerialProtocol` |
| `macrokey/` | Python 호스트 앱 패키지 (CLI + PySide6 GUI + 헤드리스 데몬) |
| `pyproject.toml` | 호스트 앱 패키징 및 의존성 |
| `manual.html` | **사용 설명서** — 연결, 키 설정, 녹화, CLI (먼저 읽으세요) |
| `wiring.html` | **배선 가이드** — 핀 맵, 결선도, 조립 순서 (인쇄용) |
| `docs/ARCHITECTURE.md` | 설계 문서 — 계층, 액션 모델, 메모리 예산, 확장 지점 |
| `docs/HARDWARE.md` | 배선 상세 — 전력 예산, 부품 근거, 빌드/업로드 |
| `docs/PROTOCOL.md` | 호스트↔장치 시리얼 프로토콜 v1 |

현재 버전: 펌웨어 `0.4.0`, 호스트 앱 `0.4.0`, 시리얼 프로토콜 `v1`, 프로필 스키마 `2`.

## 하드웨어

| 기능 | 핀 | 위치 |
| --- | --- | --- |
| 버튼 1~7 | D3~D9 (`INPUT_PULLUP`, active-low) | 아래쪽 줄 |
| 버튼 8 | D10 | 위쪽 줄 맨 끝 |
| WS2812B DIN | **A0** (330 Ω 경유) | 위쪽 줄 |
| WS2812B 5V | **VCC** (`RAW` 아님) | 위쪽 줄 |

**Pro Micro에는 D11이 없습니다.** Leonardo용 자료를 그대로 따라 하면 존재하지 않는 핀에
배선하게 됩니다. 그리고 반드시 **5 V / 16 MHz 버전** 보드여야 합니다.

배선 주의사항(직렬 저항, 커패시터, VCC/RAW 구분)과 전력 예산은
[`docs/HARDWARE.md`](docs/HARDWARE.md)에 있고, 그림으로 된 전체 결선도와 조립 순서는
[`wiring.html`](wiring.html)에 있습니다. LED 없이 버튼만 연결해도 펌웨어는 그대로
동작합니다.

## 펌웨어 빌드

Pro Micro는 `arduino:avr` 코어에 없으므로 SparkFun 보드 패키지를 먼저 추가합니다.

```bash
arduino-cli config add board_manager.additional_urls \
  https://raw.githubusercontent.com/sparkfun/Arduino_Boards/master/IDE_Board_Manager/package_sparkfun_index.json
arduino-cli core update-index
arduino-cli core install SparkFun:avr
arduino-cli lib install "Adafruit NeoPixel"
arduino-cli lib install "Keyboard"
arduino-cli lib install "Mouse"

arduino-cli compile --fqbn SparkFun:avr:promicro:cpu=16MHzatmega32U4 firmware
arduino-cli upload  --fqbn SparkFun:avr:promicro:cpu=16MHzatmega32U4 -p /dev/ttyACM0 firmware
```

`Keyboard`와 `Mouse`는 AVR 코어에 **들어 있지 않습니다.** 예전에는 번들이었지만 지금은 별도
라이브러리로 분리돼 있어서, 설치하지 않으면 `HidBackend.h`가 `Mouse.h`를 찾지 못해 빌드가
멈춥니다. Pro Micro는 리셋 버튼이
없으므로 업로드 전에 `RST`–`GND`를 빠르게 두 번 단락시켜 부트로더(8초)를 띄워야 합니다.

## 입력 모델

물리 키 8개에 **바인딩 가능한 슬롯은 키당 2개**입니다.

- `TAP` — 짧게 누름
- `DOUBLE` — 250 ms 안에 두 번
- `HOLD` — 바인딩할 수 없습니다. 3초 단독 홀드가 **녹음을 여는 방법**이고,
  탭 후 홀드는 `DOUBLE` 슬롯에 녹음합니다.

8 키 × 2 제스처 = **16 슬롯**. 레이어는 없습니다 — 보이지 않는 모드와 외워야 하는 진입
방법을 만들고, 그 대가로 EEPROM을 먹었습니다. 지금은 그 바이트를 매크로 레코드가 씁니다.

액션은 장치에서 끝나는 것(단축키, 미디어 키, 마우스, 매크로, LED 씬)과 호스트가 받아
처리하는 것(`HOST` 토큰 — 클립보드 이미지, 셸 명령, 한글 텍스트)으로 나뉩니다. 자세한 표는
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)를 참고하세요.

## 기본 프로필

펌웨어를 처음 올렸을 때, 그리고 앱을 처음 설치했을 때 양쪽이 동일하게 갖는 기본값입니다.

| 키 | 제스처 | 동작 |
| --- | --- | --- |
| 1~8 | TAP | `ctrl+alt+shift+1` ~ `ctrl+alt+shift+8` |
| 1~8 | DOUBLE | 비어 있음 — 탭 후 3초 홀드로 녹음해 채웁니다 |

바인딩된 것이 없는 채로 출고되는 슬롯이 없도록 TAP은 전부 채워 두고, DOUBLE은 비워 둡니다.
더블탭 감지는 바인딩이 있는 키만 250 ms를 기다리므로, 비어 있는 편이 빠릅니다.

`ctrl+alt+shift+숫자`를 기본으로 쓰는 이유는 이 조합을 선점하는 프로그램이 거의 없어서
기존 단축키를 뺏지 않기 때문입니다.

## 호스트 앱

설치:

```bash
python -m pip install -e ".[gui,input]"         # Linux / macOS
python -m pip install -e ".[gui,input,windows]" # Windows (클립보드 이미지 지원)
python -m pip install -e .                      # 헤드리스 (CLI만, Qt 없이)
```

`pyserial`만 필수입니다. `PySide6`(편집기 창), `pynput`(입력 합성·레코딩),
`Pillow`/`pywin32`(Windows 이미지 클립보드)는 선택이고, 없으면 해당 기능만 비활성화되고
나머지는 그대로 동작합니다. Qt는 용량이 크므로 데스크톱 없는 장비에서는 마지막 줄로 설치하세요 —
`gui`를 뺀 설치에서 `macrokey gui`를 실행하면 설치 안내를 출력하고 종료합니다.

### CLI

```bash
macrokey                     # GUI 편집기 (기본)
macrokey ports               # 시리얼 포트 목록
macrokey info                # 장치 정보 + 프로필 동기화 여부
macrokey push                # 저장된 프로필을 장치에 기록
macrokey pull [--save]       # 장치 프로필 읽기 (--save: 호스트 프로필로 채택)
macrokey monitor             # 장치 이벤트 실시간 출력
macrokey daemon              # 창 없이 호스트 액션 + LED만 실행
macrokey record --key 3 [--gesture tap|double] [--name ...]
macrokey state running [--progress 0.5] [--severity info]
```

전역 옵션: `--port <포트>`(생략 시 자동 탐색), `--verbose`, `--version`.

모든 명령이 GUI 없이 동작합니다. 데스크톱 세션이 없는 장비에서 실제 하드웨어를 상대로
테스트할 때 쓰는 경로이기도 합니다.

### 매크로 레코딩 — 패드에서 직접

**키 하나를 단독으로 3초 누르면 그 키에 녹음이 시작됩니다.** 픽셀이 빨갛게 맥동하는 동안
키보드와 마우스가 모두 캡처되고, **같은 키를 다시 3초 누르면 저장**됩니다. 앱은 켜져
있어야 하지만(캡처는 PC가 합니다) 창을 볼 필요는 없습니다.

**더블 슬롯에 녹음하려면 더블클릭한 뒤 그대로 홀드합니다** (탭 → 바로 다시 눌러 3초).
픽셀이 빨강 대신 **분홍**으로 맥동하면 더블 슬롯을 프로그래밍하는 중입니다. 이렇게 키
하나에 두 개의 매크로를 창을 열지 않고 넣을 수 있습니다.

| 동작 | 들어가는 슬롯 | 픽셀 |
| --- | --- | --- |
| 홀드 3초 | tap | 빨강 |
| 탭 → 홀드 3초 | double | 분홍 |

- "단독"이 조건입니다. 다른 키를 같이 누른 채 홀드하면 발동하지 않습니다.
- 어느 슬롯인지는 **시작할 때** 정해집니다. 끝낼 때는 같은 키이기만 하면 됩니다.
- 다른 키를 홀드해 끝내려 하면 거부되고, 시작한 키로 끝내라고 알려 줍니다.
- 저장 결과는 픽셀 색으로 구분됩니다: 초록은 패드에 저장(앱을 꺼도 동작), 주황은 호스트
  액션(이 PC가 켜져 있어야 동작), 빨강은 저장되지 않음.
- 10분이 지나면 스스로 저장하고 멈춥니다. 패드를 뽑으면 녹음은 폐기됩니다 — 어느 쪽이든
  전역 캡처가 무기한 켜져 있는 상태를 남기지 않기 위해서입니다.

**마우스가 든 녹음은 커서를 화면 좌상단으로 보내고 시작합니다.** 커널은 마우스 이동을
상대값으로만 알려주기 때문에, 기준점 없이는 재생할 때 "커서가 지금 있는 곳"에서 움직여
엉뚱한 데를 클릭합니다. 녹음도 재생도 같은 구석에서 출발하면 같은 픽셀에 떨어집니다.
커서를 옮기는 건 패드 자신입니다 — 진짜 USB 마우스라서 Wayland에서도 됩니다.

캡처된 입력은 정규화를 거칩니다: modifier 접기 (`ctrl↓ c↓ c↑ ctrl↑` → `hotkey ctrl+c`),
지연 양자화, 연속 문자 병합, 패드 자신의 HID 에코 제외, 그리고 비밀번호로 보이는 구간
제거(sudo 등을 실행하고 Enter를 친 직후의 텍스트).

저장 위치는 앱이 정합니다. 단축키 하나면 장치 액션으로, 그보다 길면 장치 매크로로,
장치가 재생할 수 없는 것(클립보드·셸·한글 등)만 호스트 액션이 됩니다. 장치에 들어가면
앱을 꺼도 패드가 혼자 재생합니다.

`macrokey record`는 같은 일을 CLI에서 하는 경로입니다(Esc로 종료). 바인딩 후
`macrokey push`로 장치에 기록합니다.

### 녹음 용량

매크로 영역은 EEPROM 1 KB 중 941바이트, **3바이트 레코드 308개**입니다. 슬롯 16개가 이
예산을 공유하고, 한 슬롯은 최대 255 레코드까지 씁니다.

타이핑한 텍스트는 헤더 1개 + 문자 3개당 레코드 1개로 저장되므로, 텍스트 기준 실질 용량은
**약 900자**입니다. 예를 들어 `sudo apt update && sudo apt upgrade -y` (38자)는 14
레코드입니다.

같은 키에 다시 녹음하면 이전 녹음이 쓰던 슬롯을 회수해 재사용합니다.

### 타이핑 속도

연속으로 친 글자는 타이밍이 기록되지 않고 `text` 하나로 합쳐지므로, **재생이 녹음보다
빠릅니다** (기본 글자당 5 ms). 받는 쪽이 따라오지 못하면 툴바의 **Typing**을 올리세요 —
글자당 대기 시간이고 프로필에 저장되므로 **앱을 꺼도 패드가 그 속도로 칩니다.**

로그는 콘솔이 아니라 `~/.config/macrokey/macrokey.log`에 남습니다. `--verbose`를 주면
콘솔에도 나옵니다.

### LED 상태 표시

WS2812B 1개는 장식이 아니라 상태 표시 장치입니다. 우선순위 합성(키 누름 피드백 >
호스트 앰비언트 > 기본 씬)으로 4개 소스를 하나의 픽셀에 겹칩니다. 픽셀이 하나라 어느 키를
눌러도 같은 반짝임이고, `progress`는 바가 아니라 밝기로 나타납니다.

호스트 앰비언트는 [AgentPet 이벤트 프로토콜 v1](https://github.com/maduinos/AgentPet/blob/main/docs/EVENT_PROTOCOL.md)의
`state`/`severity`/`progress`를 그대로 받습니다. 유닉스 소켓에 JSON 한 줄을 쓰면 어떤
스크립트든 이벤트 소스가 됩니다.

```bash
macrokey daemon &
macrokey state running --progress 0.5
```

호스트에서 3초 넘게 프레임이 오지 않으면 장치가 로컬 씬으로 되돌아갑니다. 앱을 껐을 때
LED가 마지막 색으로 얼어붙지 않습니다.

## 설정 파일 위치

프로필과 설정은 저장소 안이 아니라 사용자 설정 폴더에 저장됩니다.

| 플랫폼 | 경로 |
| --- | --- |
| Linux | `$XDG_CONFIG_HOME/macrokey` (기본 `~/.config/macrokey`) |
| macOS | `~/Library/Application Support/MaduinosMacroKey` |
| Windows | `%APPDATA%\MaduinosMacroKey` |

`profile.json`(키맵·액션)과 `settings.json`(포트·자동 연결·LED 옵션)이 들어갑니다.
`MACROKEY_CONFIG_DIR` 환경 변수로 위치를 바꿀 수 있습니다. 구버전 앱의
`bindings.json`은 처음 실행할 때 한 번 자동 마이그레이션됩니다.

## 안전 안내

HID 장치는 정의상 사용자가 알아채기 어려운 입력을 보낼 수 있습니다. 이 프로젝트는 개인
도구이므로 다음을 지킵니다.

- 부팅 직후 2초 동안 입력을 보내지 않습니다. 매크로가 잘못돼 키를 무한히 뿜는 상태가 되면
  이 2초가 재업로드할 기회입니다. 놓쳤다면 리셋 버튼을 빠르게 두 번 눌러 부트로더를 붙잡으세요.
- `SEQUENCE`/`HOST` 액션에 최대 스텝 수와 총 실행 시간 상한이 있습니다.
- 레코더는 녹화 중임을 LED 전체 빨강 pulse로 표시합니다. 조용히 기록하지 않습니다.
- 레코딩 결과는 저장 전 사람이 읽을 수 있는 형태로 보여주고 확인을 받습니다.

예상치 못한 단축키 입력이 문제가 되지 않는 환경에서 업로드하고 실행하세요.

## 라이선스

MIT License로 배포합니다. 자세한 내용은 `LICENSE`를 확인하세요.

## 프로젝트 관리

- 변경 이력: `CHANGELOG.md`
- 릴리스 절차: `RELEASE.md`
- 지원 범위: `SUPPORT.md`
- 기여 가이드: `CONTRIBUTING.md`
- 보안 신고: `SECURITY.md`
