# macroKey

Maduinos의 개인 매크로 키패드 프로젝트입니다.

버튼 8개짜리 Pro Micro 키패드를 "고정 단축키 8개"에서 제스처·레이어·LED 상태 표시를
갖춘 매크로 플랫폼으로 확장합니다. 취미/실험 프로젝트이며 Maduinos FPGA 비즈니스 포트폴리오와는
구분합니다.

핵심 원칙은 **device-first, host-optional** 입니다. 펌웨어만 올리면 PC에 아무것도 설치하지 않아도
평범한 USB HID 키보드로 동작하고, 호스트 앱은 긴 텍스트·클립보드 이미지·셸 명령·LED 상태 표시를
얹는 선택적 증폭기입니다.

## 구성

| 경로 | 설명 |
| --- | --- |
| `arduino/macrokey/` | Pro Micro(ATmega32u4, 5 V / 16 MHz) 펌웨어 |
| `arduino/macrokey/src/` | `KeyEngine`, `Profile`(EEPROM), `LedController`, `ButtonInput`, `SerialProtocol` |
| `host/macrokey/` | Python 호스트 앱 패키지 (CLI + Tkinter GUI + 헤드리스 데몬) |
| `host/macros/` | 기본 매크로 이미지 샘플 |
| `host/pyproject.toml` | 호스트 앱 패키징 및 의존성 |
| `docs/ARCHITECTURE.md` | 설계 문서 — 계층, 액션 모델, 메모리 예산, 확장 지점 |
| `docs/HARDWARE.md` | 배선, 전력 예산, 빌드/업로드 |
| `docs/wiring.html` | 그림으로 된 배선 가이드 — 핀 맵, 결선도, 조립 순서 (인쇄용) |
| `docs/manual.html` | 사용 설명서 — 키 설정, 액션 종류, 녹화, 호스트 액션, CLI |
| `docs/PROTOCOL.md` | 호스트↔장치 시리얼 프로토콜 v1 |

현재 버전: 펌웨어 `0.3.0`, 호스트 앱 `0.3.0`, 시리얼 프로토콜 `v1`.

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
[`docs/wiring.html`](docs/wiring.html)에 있습니다. LED 없이 버튼만 연결해도 펌웨어는 그대로
동작합니다.

## 펌웨어 빌드

Pro Micro는 `arduino:avr` 코어에 없으므로 SparkFun 보드 패키지를 먼저 추가합니다.

```bash
arduino-cli config add board_manager.additional_urls \
  https://raw.githubusercontent.com/sparkfun/Arduino_Boards/master/IDE_Board_Manager/package_sparkfun_index.json
arduino-cli core update-index
arduino-cli core install SparkFun:avr
arduino-cli lib install "Adafruit NeoPixel"

arduino-cli compile --fqbn SparkFun:avr:promicro:cpu=16MHzatmega32U4 arduino/macrokey
arduino-cli upload  --fqbn SparkFun:avr:promicro:cpu=16MHzatmega32U4 -p /dev/ttyACM0 arduino/macrokey
```

`Keyboard`와 `Mouse`는 AVR 코어에 포함돼 있어 따로 설치하지 않습니다. Pro Micro는 리셋 버튼이
없으므로 업로드 전에 `RST`–`GND`를 빠르게 두 번 단락시켜 부트로더(8초)를 띄워야 합니다.

## 입력 모델

물리 키 8개를 **제스처 × 레이어**로 확장합니다.

- 제스처 3종: `TAP`(짧게), `DOUBLE`(250 ms 안에 두 번), `HOLD`(400 ms 이상)
- 레이어 4개: momentary(키 홀드), toggle, host-driven(활성 창 기반)
- 4 레이어 × 8 키 × 3 제스처 = **96 슬롯**, 여기에 동시 입력 코드(chord) 8개

액션은 장치에서 끝나는 것(단축키, 미디어 키, 마우스, 레이어 전환, LED 씬)과 호스트가 받아
처리하는 것(`HOST` 토큰 — 긴 텍스트, 클립보드 이미지, 셸 명령)으로 나뉩니다. 자세한 표는
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)를 참고하세요.

## 기본 프로필

펌웨어를 처음 올렸을 때, 그리고 앱을 처음 설치했을 때 양쪽이 동일하게 갖는 기본값입니다.

| 레이어 | 키 | 동작 |
| --- | --- | --- |
| 0 (Base) | 1~8 TAP | `ctrl+alt+shift+1` ~ `ctrl+alt+shift+8` |
| 0 (Base) | 8 HOLD | 레이어 1로 momentary 전환 |
| 1 (Media) | 1~4 TAP | 볼륨 down / up / mute / play-pause |
| 1 (Media) | 5~7 TAP | 호스트 액션 토큰 0~2 |
| — | 1+2 동시 | 실행 중인 호스트 액션 중지 |

`ctrl+alt+shift+숫자`를 기본으로 쓰는 이유는 이 조합을 선점하는 프로그램이 거의 없어서
기존 단축키를 뺏지 않기 때문입니다.

## 호스트 앱

설치:

```bash
python -m pip install -e "host[input]"        # Linux / macOS
python -m pip install -e "host[input,windows]" # Windows (클립보드 이미지 지원)
```

`pyserial`만 필수이고, `pynput`(입력 합성·레코딩)과 `Pillow`/`pywin32`(Windows 이미지
클립보드)는 선택입니다. 없으면 해당 기능만 비활성화되고 나머지는 그대로 동작합니다.

### CLI

```bash
macrokey                     # GUI 편집기 (기본)
macrokey ports               # 시리얼 포트 목록
macrokey info                # 장치 정보 + 프로필 동기화 여부
macrokey push                # 저장된 프로필을 장치에 기록
macrokey pull [--save]       # 장치 프로필 읽기 (--save: 호스트 프로필로 채택)
macrokey monitor             # 장치 이벤트 실시간 출력
macrokey daemon              # 창 없이 호스트 액션 + LED만 실행
macrokey record --key 3 [--layer 0] [--gesture tap] [--name ...]
macrokey state running [--progress 0.5] [--severity info]
```

전역 옵션: `--port <포트>`(생략 시 자동 탐색), `--verbose`, `--version`.

모든 명령이 GUI 없이 동작합니다. 데스크톱 세션이 없는 장비에서 실제 하드웨어를 상대로
테스트할 때 쓰는 경로이기도 합니다.

### 매크로 레코딩

`macrokey record`는 실제 입력을 캡처해 액션 시퀀스로 정규화합니다. modifier 접기
(`ctrl↓ c↓ c↑ ctrl↑` → `hotkey ctrl+c`), 지연 양자화, 연속 문자 병합, 자기 입력 제외를
거친 뒤 사람이 읽을 수 있는 요약을 보여주고 확인을 받습니다.

정규화 결과가 장치 액션 하나로 표현되면 EEPROM에 굽고, 아니면 호스트 액션으로 저장한 뒤
슬롯에는 `HOST` 토큰만 넣습니다. 이 배치는 앱이 알아서 하므로 신경 쓸 필요가 없습니다.

바인딩 후 `macrokey push`로 장치에 기록합니다.

### LED 상태 표시

WS2812B 1개는 장식이 아니라 상태 표시 장치입니다. 우선순위 합성(키 누름 피드백 > 레이어 표시 >
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
