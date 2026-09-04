> 만든 사람: maduinos<br>
> 문서 만든 날짜: 2026-09-02<br>
> https://maduinos.blogspot.com/

# 지원 보드

macroKey는 여러 보드에서 같은 펌웨어 소스로 돕니다. 보드가 다르면 달라지는 것은 **핀,
저장소 크기와 종류, 이름, 부트로더** 넷뿐이고, 나머지 — 프로토콜, 매크로 형식, 앱, 녹음,
LED 동작 — 는 전부 공통입니다.

| 보드 | id | 저장소 | 슬롯당 최대 | 첫 플래싱 | 문서 |
| --- | --- | --- | --- | --- | --- |
| Pro Micro (ATmega32u4) | `promicro` | 1 KB EEPROM (308 레코드) | 255 | RST–GND 더블탭 | [promicro.md](boards/promicro.md) |
| ProMicro RP2040 (16 MB) | `promicro-rp2040` | 65,520 B LittleFS (21,801 레코드) | 21,800 | BOOTSEL | [promicro-rp2040.md](boards/promicro-rp2040.md) |

`id`는 펌웨어가 `HELLO board=`로 보고하는 이름이자, 펌웨어 이미지 파일 이름이자, 문서 파일
이름입니다. 셋이 어긋날 수 없도록 하나의 문자열을 씁니다.

공통 동작은 보드별로 반복하지 않습니다. 프로토콜은 [PROTOCOL.md](PROTOCOL.md), 구조는
[ARCHITECTURE.md](ARCHITECTURE.md), 보드와 무관한 하드웨어 설계(전력, LED 합성, 부팅
안전장치)는 [HARDWARE.md](HARDWARE.md)에 있습니다.

## 펌웨어 설치

**보드를 USB로 꽂고 앱을 켜면 됩니다.** 앱이 어떤 보드인지 알아보고, 맞는 펌웨어가 없거나
낡았으면 확인을 한 번 받은 뒤 직접 굽습니다. arduino-cli도, 코어도, 라이브러리도 필요
없습니다 — 완성된 이미지가 앱 안에 들어 있습니다.

```bash
macrokey flash          # 확인을 받고 자동으로
macrokey flash --yes    # 묻지 않고
```

한 번만은 손이 필요합니다. **공장 초기 상태의 보드에 처음 굽는 순간**입니다. 빈 보드에는
"부트로더로 가라"는 말을 들어줄 코드가 아직 없어서, 물리적으로 부트로더에 넣어야 합니다.
그 동작은 보드마다 달라서 각 보드 문서에 적혀 있고, 앱도 그 문장을 그대로 띄웁니다.
**두 번째 플래싱부터는 완전 자동입니다.**

소스에서 직접 빌드하려면 각 보드 문서의 `arduino-cli` 절을 보세요. 개발용이고, 쓰는 사람은
필요 없습니다.

## 새 보드 추가하기

파일 셋을 만들면 됩니다. 하나라도 빠지면 테스트가 실패하므로, 조용히 반쯤 지원되는 보드는
생기지 않습니다.

1. **`macrokey/boards.py`에 `Board` 항목 하나.**
   저장소 크기·스키마·카운트 폭·슬롯 상한, USB id(동작 중/부트로더), 플래싱 방법, FQBN,
   그리고 첫 플래싱 안내 문구. 앱에서 보드를 나열하는 곳은 여기뿐이라, 포트 인식·레이아웃
   계산·플래싱은 등록만 하면 따라옵니다.

2. **`firmware/src/boards/<board>.h` 헤더 하나.**
   `MK_BOARD_NAME`, `MK_KEY_PINS`, `MK_LED_PIN`, `MK_EEPROM_SIZE`,
   `MK_MACRO_COUNT_BYTES`, `MK_PROFILE_SCHEMA`, `MK_MACRO_MAX_RECORDS`,
   `MK_STORAGE_FLASH_EMULATED`, 그리고 부트로더 진입 방식.
   `firmware/src/boards/board.h`에 선택 분기를 추가하고, 거기 있는 계약 검사가 빠진 심볼을
   **컴파일 에러로** 잡습니다. `MK_BOARD_NAME`은 1번의 `Board.id`와 같아야 합니다.

3. **`docs/boards/<board>.md` 문서 하나.**
   배선(핀 맵), 첫 플래싱 물리 동작, 그 보드에서만 겪는 함정. 공통 내용은 쓰지 마세요 —
   여기서 반복되는 문장은 다음 보드에서 어긋납니다.

그리고 `pytest`. 다음이 자동으로 새 보드까지 검사합니다.

- `test_boards.py` — 등록 내용 자체의 정합성(중복 id/USB id 없음, 크기가 서로 다름,
  카운트 폭이 슬롯 상한을 담을 수 있음, 문서와 헤더가 실재함)
- `test_firmware_agreement.py` — 그 보드의 `-D`로 펌웨어를 **실제로 컴파일해서**, 보고하는
  레이아웃이 레지스트리와 한 바이트도 다르지 않은지, `HELLO`가 등록된 이름을 대는지

새 보드가 기존 보드와 저장소 크기가 같으면 `board_by_profile_size()`가 모호해집니다. 그때는
크기가 아니라 `HELLO board=`로만 식별되도록 테스트가 먼저 실패합니다.

## 왜 이렇게 나눴는가

보드 분기가 `Config.h`, `Profile.h`, `Storage.cpp`, `SerialProtocol.cpp`에 여섯 군데로
흩어져 있었습니다. 세 번째 보드를 넣으려면 여섯 곳을 다 찾아야 했고, 놓친 한 곳은 빌드를
깨뜨리지 않았습니다 — 조용히 AVR 분기를 타서, 없는 1 KB를 다 쓸 때까지 잘 도는 펌웨어가
됐을 겁니다.

앱 쪽도 같은 문제가 있었습니다. 연결이 끊기면 가장 작은 보드로 되돌아갔기 때문에, 21,801
레코드가 들어가는 패드가 자기 매크로를 308 기준으로 재서 "10% 사용"이라고 말했고, 308을
넘는 녹음을 거부했습니다.
