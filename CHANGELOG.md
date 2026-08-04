# Changelog

## 2026-08-04 (2) — GUI를 PySide6로 교체

- **편집기 창을 Tkinter에서 PySide6로 옮겼습니다.** `ui/app.py` 한 파일만 다시 썼고
  `config/` · `device/` · `actions/` · `recorder/` · `led/` · `backends/`는 한 줄도
  건드리지 않았습니다. "`ui/`를 제외한 어느 모듈도 UI 툴킷을 import 하지 않는다"는 규칙이
  실제로 값을 한 지점입니다.
- 화면 구성과 동작은 그대로입니다. 툴바(Port · Connect · Disconnect · Save · Write to device ·
  Read from device · Brightness), 레이어 탭 4개, 키 8행 × 제스처 3열 = 96슬롯 격자, 상태 표시줄,
  슬롯 편집·녹화 다이얼로그, 값 검증 시점(OK 누를 때) 모두 동일합니다.
- 스레드→UI 전달 방식이 `root.after(0, ...)`에서 **Qt 시그널**로 바뀌었습니다
  (`statusMessage` · `failed` · `pulled`). 장치 통신은 이전과 같이 평범한 스레드에서 돕니다.
- **`PySide6`를 선택 의존성 `gui`로 추가했습니다.** Qt는 용량이 크고 헤드리스 사용 경로가
  실재하므로 필수로 넣지 않았습니다. 없으면 `macrokey gui`만 설치 안내(`MissingToolkit`)와
  함께 종료 코드 2로 끝나고 나머지 명령은 그대로 동작합니다.
  설치는 `pip install -e ".[gui,input]"`입니다.
- `cli.py`가 `from .ui.app import run_gui` 대신 `from .ui import run_gui`를 씁니다.
  안내 메시지를 담은 얇은 진입점을 거치게 하기 위해서입니다.

## 2026-08-04

저장소 레이아웃을 평탄화하고 사용 문서를 최상위로 올렸습니다. 코드 동작은 그대로입니다.

- **`arduino/macrokey/` → `firmware/`**, 스케치 이름도 `macrokey.ino` → `firmware.ino`.
  아두이노는 스케치 파일 이름이 폴더 이름과 같아야 하므로 함께 바뀝니다.
  빌드는 이제 `arduino-cli compile ... firmware`입니다.
- **`host/macrokey/` → `macrokey/`**, `host/pyproject.toml` → `pyproject.toml`.
  중간 `host/` 계층이 없어져 파이썬 앱 폴더로 바로 들어갑니다.
  설치는 `pip install -e ".[input]"`입니다.
- **`docs/manual.html` → `manual.html`, `docs/wiring.html` → `wiring.html`.**
  가장 자주 여는 두 문서를 최상위로 올리고 내용을 요약했습니다
  (사용 설명서 1042 → 375줄, 배선 가이드 1070 → 504줄). 핀 맵과 전체 결선도 그림은
  그대로 두고 설명 문장, 화면 목업, 레시피 모음, 중복된 전력 예산 절을 덜어냈습니다.
  전력 계산 근거는 `docs/HARDWARE.md`에 그대로 있습니다.
- **`host/macros/*.png` 샘플 이미지 8개를 삭제했습니다.** 구버전 "단축키 → 이미지 붙여넣기"
  앱의 잔재이고 코드가 참조하지 않았습니다. `clipboard_image` 액션과 구버전
  `bindings.json` 마이그레이션은 그대로 남아 있습니다.
- 위 삭제에 따라 `resolve_asset()`의 상대 경로 기준을 저장소 경로에서 **설정 폴더**
  (`profile.json`이 있는 곳)로 바꿨습니다. 기존 기준 폴더에는 이제 참조할 자산이 없고,
  설치본에서는 애초에 의미가 없는 경로였습니다. 절대 경로와 `~`는 이전과 동일합니다.

## 2026-08-03

저장소를 `arduino/` · `host/` · `docs/` 세 트리로 분리하고, 펌웨어와 호스트 앱을 모두
재작성했습니다. 펌웨어 및 호스트 앱 버전 `0.3.0`, 시리얼 프로토콜 `v1`.

### 펌웨어 (`arduino/macrokey/`)

- 단일 스케치 `05_macrokey.ino`를 모듈 구조로 재작성했습니다.
  `KeyEngine` · `Profile`(EEPROM) · `LedController` · `LedEffects` · `ButtonInput` ·
  `SerialProtocol` · `HidBackend` · `ActionTypes` · `Config` · `Util`.
- `TAP` / `DOUBLE` / `HOLD` 제스처와 레이어 4개를 추가해 키 8개를 96 슬롯으로 확장했습니다.
  동시 입력 코드(chord) 테이블 8개를 함께 넣었습니다.
- 액션 타입 12종을 도입했습니다 (`KEY`, `CONSUMER`, `MOUSE_*`, `LAYER_*`, `SEQUENCE`,
  `HOST`, `LED_SCENE`, `DELAY`). 액션은 4바이트 고정 폭이고 키맵은 RAM 미러링 없이
  EEPROM에서 직접 읽습니다.
- EEPROM 프로필 저장소(헤더 + 키맵 + 코드 + 팔레트 + 장치 매크로, CRC16 포함, 1016 B)를
  추가했습니다. 매크로를 바꿀 때 펌웨어를 다시 빌드하지 않습니다.
- WS2812 8픽셀 지원(D11)을 추가했습니다. 우선순위 합성, 50 Hz 상한, 변경 시에만 송출,
  400 mA 전류 상한 스케일링, 기본 밝기 25%.
- 호스트 LED 소유권에 3초 워치독을 넣어 앱 종료 시 마지막 색으로 얼어붙지 않게 했습니다.
- 부팅 후 2초 HID 유예 시간을 추가했습니다.

### 호스트 앱 (`host/macrokey/`)

- 단일 파일 Tkinter 앱(`python_app/macro_key_app.py`)을 `macrokey` 패키지로 재작성했습니다.
  `config/` · `device/` · `actions/` · `recorder/` · `led/` · `backends/` · `ui/`.
- CLI를 추가했습니다: `gui`, `ports`, `info`, `push`, `pull`, `monitor`, `daemon`,
  `record`, `state`. 모든 명령이 GUI 없이 동작합니다.
- 시리얼 클라이언트(포트 자동 탐색, 자동 재연결, 프로토콜 v1 코덱)를 추가했습니다.
- 매크로 레코더를 추가했습니다. modifier 접기, 지연 양자화, 연속 문자 병합, 자기 입력 제외를
  거쳐 장치 액션 또는 호스트 액션으로 자동 배치합니다.
- 호스트 액션 registry를 추가했습니다 (`hotkey`, `text`, `clipboard_image`, `shell`,
  `sequence`, `delay`, `layer`, `noop`). 새 액션은 `@register` 한 줄로 늘어납니다.
- LED 서비스와 AgentPet 이벤트 프로토콜 v1 소스를 추가했습니다. `state`/`severity`/
  `progress`를 색·효과·진행률 바로 매핑합니다.
- 플랫폼 백엔드(클립보드·활성 창·키보드)를 런타임 선택으로 분리했습니다. 기존 앱이
  `win32clipboard`를 최상위에서 import 해 Windows 밖에서 이미지 기능이 통째로 죽던 문제를
  해결했습니다. 이제 없는 기능은 해당 액션 하나만 실패합니다.
- `ui/`를 제외한 모든 모듈이 tkinter를 import 하지 않습니다. 헤드리스 실행이 가능합니다.
- 프로필/설정을 플랫폼별 표준 설정 폴더에 저장하고, 구버전 `bindings.json`을 한 번
  자동 마이그레이션합니다. `MACROKEY_CONFIG_DIR`로 위치를 바꿀 수 있습니다.
- `pyproject.toml`로 패키징했습니다. `pyserial`만 필수이고 `input`(pynput),
  `windows`(Pillow, pywin32), `dev`는 선택 의존성입니다.

### 문서

- `docs/ARCHITECTURE.md` — 설계 원칙, 계층 구조, 입력·액션 모델, 메모리 예산,
  LED 파이프라인, 확장 지점, 의도적으로 하지 않은 것.
- `docs/HARDWARE.md` — 핀 맵, 배선 주의사항, 전력 예산, WS2812와 USB 타이밍, 빌드/업로드.
- `docs/PROTOCOL.md` — 시리얼 프로토콜 v1 메시지 규격.
- `docs/manual.html` — 사용 설명서. 처음 연결, 제스처·레이어 조작 모델, 기본 프로필,
  GUI 키 설정 절차, 액션 종류와 단축키 문법, 매크로 녹화, 호스트 액션 JSON 작성법,
  CLI 명령, LED 상태 매핑, 파일 위치, 레시피 6개, 그리고 **아직 안 되는 것** 목록.
- `docs/wiring.html` — 그림으로 된 배선 가이드. Leonardo 핀 맵, 버튼 1개 상세 회로,
  WS2812B 결선(330 Ω · 커패시터 위치 포함), 전체 결선도, 전력 예산 그래프, 증상별 원인 표,
  부품 목록. 라이트/다크 테마와 A4 인쇄를 모두 지원합니다.
### 하드웨어 확정 — Pro Micro · WS2812B 1개

실제 부품이 Pro Micro와 WS2812B 1개로 정해져서 펌웨어·호스트·문서를 전부 맞췄습니다.

- **보드를 Pro Micro(5 V / 16 MHz)로 확정.** `MK_BOARD_NAME`을 `promicro`로 바꿨습니다.
- **LED 데이터 핀을 D11 → A0(디지털 18)로 변경.** Pro Micro는 D11·D12·D13을 아예 밖으로
  빼지 않아서 기존 설정으로는 배선이 불가능했습니다. 노출된 핀 중 D2/D3은 SDA/SCL,
  D14/D15/D16은 SPI라 어느 버스와도 겹치지 않는 A0를 골랐습니다. VCC·GND와 같은 쪽 헤더라
  배선도 한쪽으로 정리됩니다. 버튼(D3–D10)은 Pro Micro에도 전부 있어 그대로입니다.
- **`MK_LED_COUNT`를 8 → 1로,** 호스트 `LED_COUNT`도 함께 1로 내렸습니다. EEPROM 팔레트가
  96 B에서 12 B로 줄어 프로필 전체가 1016 B → 932 B가 됐습니다.
- **버그 수정: 픽셀보다 키가 많을 때 대부분의 키가 LED 피드백을 잃던 문제.**
  `LedController::notePress`가 `key < MK_LED_COUNT`일 때만 반응해서, 1픽셀에서는 1번 키를
  누를 때만 LED가 반짝였습니다. 이제 남는 키는 마지막 픽셀로 접습니다.
- **버그 수정: 팔레트 직렬화가 인접 영역을 덮어쓰던 문제.** `binary.py`가 팔레트 주소를
  픽셀 수로 계산하면서 키 수만큼 루프를 돌아, `LED_COUNT < KEY_COUNT`이면 다음 레이어와
  매크로 영역까지 침범했습니다. 이제 `key_index < LED_COUNT`인 슬롯만 씁니다.
- `MK_LED_MAX_MILLIAMPS`를 400 → 100으로 조정했습니다. 1픽셀은 60 mA를 넘길 수 없어 실제로는
  걸리지 않지만, `MK_LED_COUNT`를 올리면 바로 안전장치로 동작하도록 남겨 뒀습니다.
- 커패시터 권장값을 220 µF → **100 µF**로 낮췄습니다(픽셀 1개 기준).
- 빌드 절차를 SparkFun 보드 패키지 기준으로 바꿨습니다
  (`SparkFun:avr:promicro:cpu=16MHzatmega32U4`). Pro Micro는 리셋 버튼이 없어 `RST`–`GND`를
  빠르게 두 번 단락시켜 부트로더를 띄워야 한다는 안내를 추가했습니다.
- 문서에 **픽셀 1개의 표현력 한계**를 명시했습니다. 키별 위치 표시는 불가능하고,
  `progress`는 진행률 바가 아니라 밝기로 나타납니다. LED의 주된 역할은 레이어 표시입니다.
- `docs/wiring.html`의 그림 1·3·4를 Pro Micro와 단일 픽셀 기준으로 다시 그리고,
  그림 5(전력)를 "USB 예산 대비 실제 소비"로 바꿨습니다.

### 전원 전제

- 전원 전제를 **PC USB 단독 급전**으로 명시하고 문서 전체를 여기에 맞췄습니다.
  - WS2812B 커패시터 권장값을 1000 µF에서 **220 µF**(100–470 µF)로 낮췄습니다. 1000 µF는
    전용 어댑터 기준값이고, USB 2.0은 장치 벌크 커패시턴스를 10 µF로 제한하기 때문에
    돌입 전류로 포트 보호가 걸릴 수 있습니다.
  - 포트 종류별 가용 전류표(USB 2.0 / 3.x / 무전원 허브 / 열거 전)와 케이블 전압 강하
    주의를 추가했습니다.
  - 외부 5 V 전원 절은 "지금은 필요 없음, 픽셀 16개 이상으로 늘릴 때의 메모"로 격하했습니다.
  - `Config.h`의 `MK_LED_MAX_MILLIAMPS` 주석에 400 mA 근거(`LED 400 + MCU 30 = 430`)와
    무전원 허브에서 250으로 낮추라는 안내를 적었습니다.
- README를 새 구조에 맞춰 다시 썼습니다.

## 2026-05-30

- Merged the Arduino keypad and Python macro helper into one `macroKey` project.
- Added a Tkinter Python app for editable hotkey-to-image bindings.
- Added README documentation for the Arduino HID macro keypad lab.
- Added license, contribution guidance, and security policy.
- Replaced the inline scan interval literal with `SCAN_INTERVAL_MS`.
