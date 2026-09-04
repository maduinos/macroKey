> 만든 사람: maduinos<br>
> 문서 만든 날짜: 2026-09-05<br>
> https://maduinos.blogspot.com/

# 소스에서 빌드하기

**릴리스를 받아 쓰는 사람에게는 이 문서가 필요 없습니다.** 소스를 직접 빌드하거나 고칠
사람을 위한 문서입니다. 쓰는 법은 [README](../README.md)에 있습니다.

## 저장소 구조

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
│   ├── setup_linux_serial.sh # Ubuntu USB 시리얼 권한 설정
│   └── pyinstaller_hooks/
├── docs/                   # 사용·배선·설계
└── assets/                 # 앱 아이콘 (tools/make_icon.py가 생성)
```

보드는 `macrokey/boards.py` 하나에서 파생됩니다. 보드를 추가할 때의 체크리스트는
[BOARDS.md](BOARDS.md)에 있습니다.

## PC 앱 실행

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Ubuntu에서 앱이 Pro Micro/RP2040의 USB 시리얼 포트를 열 수 있게 한 번 실행합니다. 이미
`dialout` 그룹에 속한 계정에는 아무 변경도 하지 않습니다. 실행 후에는 로그아웃했다가 다시
로그인해야 합니다. 키보드·마우스 HID 동작만 쓸 때는 필요하지 않습니다.

```bash
./tools/setup_linux_serial.sh
# 로그인 계정을 자동 판별할 수 없는 환경에서는:
./tools/setup_linux_serial.sh <ubuntu-user>
```

개발용 CLI:

```bash
python -m macrokey --help    # 배포 바이너리에는 GUI만 들어갑니다
macrokey boards              # 이 빌드가 아는 보드와 가진 펌웨어
macrokey ports               # 시리얼 포트 (ttyACM 번호를 믿지 말 것)
macrokey flash [--yes]       # 펌웨어 설치
macrokey update [--check|--firmware|--offline]
```

## 배포용 실행 파일

```bash
./build_release.sh                 # 또는 --skip-tests
# Linux  → releases/linux/macrokey
# Windows→ releases/windows/macrokey.exe
```

Python 3.10 이상이 필요합니다. 가상환경을 활성화하지 않아도 빌드 스크립트가 필요한 패키지를
`.build-deps/`에 설치하므로 시스템 Python 환경은 변경하지 않습니다.

## 펌웨어 빌드

앱에는 보드별 완성 이미지가 이미 번들되어 있어서, 펌웨어를 고칠 때만 필요합니다.

```bash
./tools/build_firmware.sh                  # 등록된 모든 보드
./tools/build_firmware.sh promicro-rp2040  # 하나만
```

보드별 `arduino-cli` 명령과 FQBN은 각 보드 문서에 있습니다 —
[promicro.md](boards/promicro.md) · [promicro-rp2040.md](boards/promicro-rp2040.md).

## 릴리스

`.github/workflows/release.yml`이 main 푸시마다 Linux/Windows 실행 파일과 보드별 펌웨어를
빌드해 올립니다. main 푸시는 `latest` 롤링 프리릴리스, `v*` 태그는 정식 릴리스입니다.

버전은 `pyproject.toml`, `macrokey/__init__.py`, `firmware/src/Config.h`를 **함께** 올립니다.
앱과 펌웨어는 프로필 스키마를 공유하므로 한쪽만 뒤처지면 조용히 어긋납니다.

`.build/`, `releases/`, 로컬 `profile.json`은 커밋하지 않습니다.

## 더 읽을 것

| 문서 | 내용 |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 내부 설계 |
| [PROTOCOL.md](PROTOCOL.md) | 시리얼 프로토콜 |
| [BOARDS.md](BOARDS.md) | 새 보드 추가 체크리스트 |
| [HARDWARE.md](HARDWARE.md) | 전력 예산, LED 합성, 부팅 안전장치 |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | 기여 |
