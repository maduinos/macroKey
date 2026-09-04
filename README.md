# macroKey

8버튼 매크로 키패드. **패드는 앱 없이 USB 키보드/마우스로 동작**하고,
PC 앱은 설정·녹음할 때만 켭니다.

지원 보드는 Pro Micro(ATmega32u4)와 ProMicro RP2040이고, 같은 펌웨어 소스로 돕니다.
보드별 배선·플래싱은 [`docs/BOARDS.md`](docs/BOARDS.md)에 있습니다.

버전: 펌웨어 `0.9.3` / 앱 `0.11.1`

## 구성

```text
macroKey/
├── main.py                 # PC 앱 실행 (GUI)
├── requirements.txt
├── build_release.sh        # → releases/linux/macrokey
├── macrokey/               # 설정 앱 코드
│   ├── boards.py           # 지원 보드 레지스트리 (여기 하나에서 전부 파생)
│   └── flash/              # 보드 자동 인식 + 펌웨어 설치
├── firmware/               # 펌웨어 (보드 공용)
│   └── src/boards/         # 보드별 헤더 — 핀·저장소·부트로더
├── tests/
├── tools/
│   ├── build_firmware.sh   # 등록된 모든 보드의 펌웨어 빌드
│   ├── make_icon.py        # assets/app_icon.png + .ico 생성
│   └── setup_linux_serial.sh # Ubuntu USB 시리얼 권한 설정
├── docs/                   # 사용·배선·설계 (필요할 때)
│   ├── manual.html
│   ├── BOARDS.md           # 지원 보드 + 새 보드 추가 체크리스트
│   ├── boards/             # 보드별 배선·플래싱
│   ├── HARDWARE.md
│   ├── PROTOCOL.md
│   └── ARCHITECTURE.md
├── assets/                 # 앱 아이콘 (tools/make_icon.py가 생성)
└── tools/pyinstaller_hooks/
```

## PC 앱

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Ubuntu에서 설정 앱이 Pro Micro/RP2040의 USB 시리얼 포트를 열 수 있게 한 번 실행합니다.
이미 `dialout` 그룹에 속한 계정에는 아무 변경도 하지 않습니다. 실행 후에는 로그아웃했다가
다시 로그인해야 합니다. 키보드·마우스 HID 동작만 쓸 때는 필요하지 않습니다.

```bash
./tools/setup_linux_serial.sh
# 로그인 계정을 자동 판별할 수 없는 환경에서는:
./tools/setup_linux_serial.sh <ubuntu-user>
```

배포용 단일 실행 파일:

```bash
./build_release.sh                 # 또는 --skip-tests
# Linux  → releases/linux/macrokey
# Windows→ releases/windows/macrokey.exe
```

빌드에는 Python 3.10 이상이 필요합니다. 가상환경을 활성화하지 않아도 빌드 스크립트가
필요한 패키지를 `.build-deps/`에 설치하므로 시스템 Python 환경은 변경하지 않습니다.

언어는 **도움말 > 언어**에서 고릅니다. 기본값은 시스템 로케일 자동 감지이고, 지금은 한국어와
영어를 지원합니다. 창의 모든 문구와 버튼 폭이 창을 만들 때 정해지므로 **바꾼 언어는 다음 실행부터**
적용됩니다. 앱이 그렇게 안내합니다.

Wayland에서 녹음이 안 되면 첫 실행 안내에 따라 `input` 권한을 한 번 허용하세요. 이 권한은
계정의 프로그램이 비밀번호를 포함한 모든 키보드·마우스 입력을 읽을 수 있는 강한 권한입니다.
macroKey는 빨간 녹화 표시가 켜진 동안에만 입력 장치를 엽니다.

## 쓰는 법

1. 앱 실행 → 패드 연결
2. 키 칸에서 단축키를 넣거나, **패드 키 3초 홀드**로 녹음
3. 같은 키를 다시 3초 홀드하면 저장 (더블 슬롯 녹화는 탭 후 250 ms 안에 다시 홀드)
4. 앱 종료 — 패드는 HID로 계속 동작

포인터 가속을 끄면(flat) 마우스 매크로가 녹화된 위치에 더 정확히 떨어집니다. 앱이 한 번 물어보고,
승낙하면 데스크톱 설정을 **영구적으로** 바꿉니다 — Linux는 GNOME `accel-profile`, Windows는
"포인터 정확도 향상"입니다. 되돌리는 방법은 그 대화상자가 플랫폼에 맞게 알려줍니다.

마우스 녹화는 기본적으로 **현재 포인터 기준**입니다. 클릭은 현재 위치에서, 이동·드래그는
현재 위치로부터 상대적으로 재생됩니다. 화면의 같은 위치를 꼭 눌러야 할 때만
실험 기능인 `Fixed screen`을 켜세요. 모니터 배치·배율·포인터 가속·창 위치가 바뀌면
고정 위치 매크로도 어긋날 수 있습니다.

매크로(시퀀스) 재생 중에는 픽셀이 **시안**으로 맥동하고, 끝나면 **초록**으로 한 번
깜빡입니다.

자세한 UI 설명: [`docs/manual.html`](docs/manual.html)

## 펌웨어

**키패드를 USB로 꽂고 앱을 켜면 됩니다.** 앱이 어떤 보드인지 알아보고, macroKey 펌웨어가
없으면 확인을 한 번 받은 뒤 직접 설치합니다. arduino-cli도 코어도 라이브러리도 필요
없습니다 — 완성된 이미지가 실행 파일 안에 들어 있습니다.

```bash
macrokey flash          # 확인을 받고 자동으로
macrokey flash --yes    # 묻지 않고
macrokey boards         # 이 빌드가 아는 보드와 가진 펌웨어
```

### 업데이트

**앱과 펌웨어는 스스로 최신을 따라갑니다.** 앱은 시작할 때 GitHub 릴리스를 한 번 확인해
새 버전이 있으면 내려받아 설치하고(적용은 다음 실행), 패드를 연결할 때마다 패드가 실행 중인
펌웨어가 앱이 가진 것보다 낮으면 그 자리에서 올려 씁니다. 이 조합이 중요한 이유는 하나입니다 —
프로필 레이아웃은 앱과 펌웨어가 **같은 스키마로 합의해야** 하고, 한쪽만 뒤처지면 조용히
어긋납니다.

받은 파일은 릴리스가 함께 올린 `SHA256SUMS.txt`와 대조한 뒤에만 씁니다. 해시가 없거나
맞지 않으면 아무것도 남기지 않습니다.

- 펌웨어 버전은 **이미지 안에서** 읽습니다(`HELLO`가 출력하는 `fw=` 문자열). 파일 이름이나
  태그가 아니라 실제로 구워질 바이트가 기준입니다.
- 보통은 인터넷이 필요 없습니다. 보드별 이미지가 실행 파일 안에 들어 있어서, 패드를 앱과
  같은 수준으로 맞추는 일은 오프라인에서 끝납니다. 네트워크는 **새 앱**을 가져올 때,
  그리고 이 빌드에 이미지가 없을 때만 씁니다.
- 녹음 중이거나 프로필을 주고받는 중에는 굽지 않고 다음 연결로 미룹니다.

```bash
macrokey update            # 펌웨어와 앱, 둘 다
macrokey update --check    # 무엇이 있는지만 보고 아무것도 쓰지 않음
macrokey update --firmware # 패드만
macrokey update --offline  # 내장 이미지만 쓰고 네트워크는 건드리지 않음
```

**도움말 > 업데이트 확인**으로 언제든 직접 확인할 수 있고, 같은 메뉴에서 자동 업데이트를
끌 수 있습니다. `MACROKEY_NO_UPDATE=1`은 앱 전체의 네트워크 확인을 막습니다 —
배포판이 업데이트를 직접 관리하거나, 앱이 스스로 외부에 접속하지 않기를 바랄 때 씁니다.

한 번만은 손이 필요합니다. **공장 초기 상태의 보드에 처음 굽는 순간**입니다. 빈 보드에는
"부트로더로 가라"는 말을 들어줄 코드가 아직 없습니다 — RP2040은 BOOTSEL을 누른 채 꽂고,
Pro Micro는 `RST`–`GND`를 빠르게 두 번 단락시킵니다. 앱이 그 문장을 띄우고 부트로더가
나타나는 것을 지켜보다가 즉시 굽습니다. **두 번째부터는 완전 자동입니다.**

| 보드 | 저장소 | 매크로 영역 | 첫 플래싱 |
| --- | --- | --- | --- |
| Pro Micro (ATmega32u4) | 1,024 B / schema 2 | 308 레코드 | RST–GND 더블탭 |
| ProMicro RP2040 (16 MB) | 65,520 B / schema 3 | 21,801 레코드 | BOOTSEL |

앱은 `HELLO board=`로 보드를 알아보고, 연결이 끊긴 뒤에도 마지막 보드를 기억합니다.

0.9.1부터 패드는 저장된 프로필을 읽지 못해 공장 기본값으로 되돌렸을 때 그 사실을
`HELLO`의 `reset=1`로 알립니다. 앱은 그걸 보고 "가져오기"가 아니라 "보내기"를 권합니다.

펌웨어를 직접 빌드하려면(개발용):

```bash
./tools/build_firmware.sh                  # 등록된 모든 보드
./tools/build_firmware.sh promicro-rp2040  # 하나만
```

## 참고

| 문서 | 내용 |
| --- | --- |
| [`docs/manual.html`](docs/manual.html) | 사용 설명 |
| [`docs/BOARDS.md`](docs/BOARDS.md) | 지원 보드, 펌웨어 설치, 새 보드 추가 체크리스트 |
| [`docs/boards/promicro.md`](docs/boards/promicro.md) | 핀맵·조립·플래싱 (Pro Micro / ATmega32u4) |
| [`docs/boards/promicro-rp2040.md`](docs/boards/promicro-rp2040.md) | 핀맵·조립·플래싱 (ProMicro RP2040) |
| [`docs/HARDWARE.md`](docs/HARDWARE.md) | 전력·부품 (보드 공통) |
| [`docs/PROTOCOL.md`](docs/PROTOCOL.md) | 시리얼 프로토콜 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 내부 설계 |

개발용 CLI: `python -m macrokey --help` (배포 바이너리에는 GUI만).
