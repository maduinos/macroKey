# macroKey

8버튼 Pro Micro 매크로 키패드. **패드는 앱 없이 USB 키보드/마우스로 동작**하고,
PC 앱은 설정·녹음할 때만 켭니다.

버전: 펌웨어 `0.7.0` / 앱 `0.7.0`

## 구성

```text
macroKey/
├── main.py                 # PC 앱 실행 (GUI)
├── requirements.txt
├── build_release.sh        # → releases/linux/macrokey
├── macrokey/               # 설정 앱 코드
├── firmware/               # Pro Micro 스케치
├── tests/
├── docs/                   # 사용·배선·설계 (필요할 때)
│   ├── manual.html
│   ├── wiring.html
│   ├── HARDWARE.md
│   ├── PROTOCOL.md
│   └── ARCHITECTURE.md
├── assets/                 # 선택: 앱 아이콘
└── tools/pyinstaller_hooks/
```

## PC 앱

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

배포용 단일 실행 파일:

```bash
./build_release.sh                 # 또는 --skip-tests
# Linux  → releases/linux/macrokey
# Windows→ releases/windows/macrokey.exe
```

Wayland에서 녹음이 안 되면 첫 실행 안내에 따라 `input` 권한을 한 번 허용하세요. 이 권한은
계정의 프로그램이 비밀번호를 포함한 모든 키보드·마우스 입력을 읽을 수 있는 강한 권한입니다.
macroKey는 빨간 녹화 표시가 켜진 동안에만 입력 장치를 엽니다.

## 쓰는 법

1. 앱 실행 → 패드 연결
2. 키 칸에서 단축키를 넣거나, **패드 키 3초 홀드**로 녹음
3. 같은 키를 다시 3초 홀드하면 저장 (더블 슬롯 녹화는 탭 후 250 ms 안에 다시 홀드)
4. 앱 종료 — 패드는 HID로 계속 동작

마우스 녹화는 기본적으로 **현재 포인터 기준**입니다. 클릭은 현재 위치에서, 이동·드래그는
현재 위치로부터 상대적으로 재생됩니다. 화면의 같은 위치를 꼭 눌러야 할 때만
실험 기능인 `Fixed screen`을 켜세요. 모니터 배치·배율·포인터 가속·창 위치가 바뀌면
고정 위치 매크로도 어긋날 수 있습니다.

매크로(시퀀스) 재생 중에는 픽셀이 **시안**으로 맥동하고, 끝나면 **초록**으로 한 번
깜빡입니다.

자세한 UI 설명: [`docs/manual.html`](docs/manual.html)

## 펌웨어

```bash
arduino-cli compile --upload --fqbn SparkFun:avr:promicro:cpu=16MHzatmega32U4 \
  -p /dev/ttyACM0 firmware
```

보드: **5 V / 16 MHz Pro Micro**. 배선은 [`docs/wiring.html`](docs/wiring.html).  
업로드 전 `RST`–`GND` 더블탭으로 부트로더를 띄우세요.

## 참고

| 문서 | 내용 |
| --- | --- |
| [`docs/manual.html`](docs/manual.html) | 사용 설명 |
| [`docs/wiring.html`](docs/wiring.html) | 핀맵·조립 |
| [`docs/HARDWARE.md`](docs/HARDWARE.md) | 전력·부품 |
| [`docs/PROTOCOL.md`](docs/PROTOCOL.md) | 시리얼 프로토콜 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 내부 설계 |

개발용 CLI: `python -m macrokey --help` (배포 바이너리에는 GUI만).
