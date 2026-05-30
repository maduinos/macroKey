# macroKey

Maduinos의 개인 매크로 키패드 프로젝트입니다.

이 저장소는 Arduino HID 키패드 펌웨어와 Windows용 Python 매크로 GUI 앱을 함께 관리합니다. 취미/실험 프로젝트이며 Maduinos FPGA 비즈니스 포트폴리오와는 구분합니다.

## 구성

| 경로 | 설명 |
| --- | --- |
| `arduino/05_macrokey/05_macrokey.ino` | Arduino Leonardo/Micro/Pro Micro 계열 HID 키패드 스케치 |
| `python_app/macro_key_app.py` | Tkinter 기반 매크로 이미지 바인딩 앱 |
| `python_app/macros/` | 기본 매크로 이미지 샘플 |
| `python_app/requirements.txt` | Python 앱 의존성 |

## Arduino 키패드

스케치는 active-low 버튼 입력 8개를 읽고 Arduino `Keyboard` 라이브러리로 `Tab + 숫자` 단축키를 보냅니다.

| Pin | Shortcut |
| --- | --- |
| D3 | `Tab + 1` |
| D4 | `Tab + 2` |
| D5 | `Tab + 3` |
| D6 | `Tab + 4` |
| D7 | `Tab + 5` |
| D8 | `Tab + 6` |
| D9 | `Tab + 7` |
| D10 | `Tab + 8` |

요구 사항:

- Leonardo, Micro, Pro Micro처럼 native USB HID keyboard mode를 지원하는 Arduino board
- Arduino IDE 또는 `arduino-cli`

빌드 예:

```bash
cd arduino/05_macrokey
arduino-cli compile --fqbn arduino:avr:leonardo .
```

## Python 매크로 앱

Python 앱은 Tkinter 화면에서 매크로 이름, 단축키, 이미지 파일, 자동 붙여넣기 여부, Enter 입력 여부를 편집할 수 있습니다.

요구 사항:

- Windows
- Python 3.10+
- `pynput`
- `Pillow`
- `pywin32`

설치:

```bash
cd python_app
python -m pip install -r requirements.txt
```

실행:

```bash
python macro_key_app.py
```

기본 바인딩:

| Hotkey | 기본 이미지 |
| --- | --- |
| `tab+1` | `macros/macro_1.png` |
| `tab+2` | `macros/macro_2.png` |
| `tab+3` | `macros/macro_3.png` |
| `tab+4` | `macros/macro_4.png` |
| `tab+5` | `macros/macro_5.png` |
| `tab+6` | `macros/macro_6.png` |
| `tab+7` | `macros/macro_7.png` |
| `tab+8` | `macros/macro_8.png` |

앱 설정은 저장소 안이 아니라 사용자 설정 폴더에 저장됩니다.

- Windows: `%APPDATA%\MaduinosMacroKey\bindings.json`
- 그 외 환경: `~/MaduinosMacroKey/bindings.json`

## 안전 안내

Arduino 스케치와 Python 앱 모두 host computer에 keyboard input을 보냅니다. 예상치 못한 단축키 입력이 문제가 되지 않는 환경에서 업로드하고 실행하세요.

## 라이선스

MIT License로 배포합니다. 자세한 내용은 `LICENSE`를 확인하세요.

## 프로젝트 관리

- 변경 이력: `CHANGELOG.md`
- 릴리스 절차: `RELEASE.md`
- 지원 범위: `SUPPORT.md`
- 기여 가이드: `CONTRIBUTING.md`
- 보안 신고: `SECURITY.md`
