# macroKey

Maduinos의 개인 Arduino HID 매크로 키패드 스케치입니다.

이 저장소는 취미/실험 프로젝트이며 Maduinos FPGA 비즈니스 포트폴리오에 포함되지 않습니다.

## 기능

이 스케치는 active-low 버튼 입력 8개를 읽고 Arduino `Keyboard` library를 통해 `Tab + 숫자` 단축키를 보냅니다.

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

## 요구 사항

- Leonardo, Micro, Pro Micro처럼 native USB HID keyboard mode를 지원하는 Arduino board
- Arduino IDE 또는 `arduino-cli`

## 빌드

Arduino IDE에서 `05_macrokey.ino`를 열고 USB HID를 지원하는 board를 선택합니다.

`arduino-cli`가 설치되어 있다면:

```bash
arduino-cli compile --fqbn arduino:avr:leonardo .
```

## 안전 안내

이 스케치는 host computer에 keyboard input을 보냅니다. 예상치 못한 단축키 입력이 문제가 되지 않는 환경에서 업로드하고 테스트하세요.

## 라이선스

MIT License로 배포합니다. 자세한 내용은 `LICENSE`를 확인하세요.

## 프로젝트 관리

- 변경 이력: `CHANGELOG.md`
- 릴리스 절차: `RELEASE.md`
- 지원 범위: `SUPPORT.md`
- 기여 가이드: `CONTRIBUTING.md`
- 보안 신고: `SECURITY.md`
