# macroKey 시리얼 프로토콜 v1

호스트 앱과 펌웨어가 USB CDC 위에서 주고받는 규격입니다. 설계 배경은
[`ARCHITECTURE.md`](ARCHITECTURE.md)를 참고하세요.

## 1. 링크

| 항목 | 값 |
| --- | --- |
| 전송 | USB CDC (ATmega32u4 native USB, `Serial`) |
| 속도 | 115200 8N1 (CDC라 실제 보율은 무시되지만 관례로 맞춥니다) |
| 인코딩 | ASCII |
| 프레이밍 | 한 줄에 메시지 하나, `\n` 종료 (`\r`은 무시) |
| 최대 줄 길이 | 96 바이트 (초과분은 버리고 `ERR code=long` 응답) |
| 프로토콜 버전 | `1` |

HID 입력과 시리얼은 완전히 독립입니다. 시리얼을 아무도 열지 않아도 키패드는 동작합니다.

## 2. 메시지 문법

```
<VERB> [key=value ...]
```

- `VERB`는 대문자, 키는 소문자입니다.
- 값에는 공백이 없습니다. 문자열이 필요하면 base64 또는 hex로 보냅니다.
- 알 수 없는 키는 무시합니다(전방 호환). 알 수 없는 `VERB`는 `ERR code=verb`입니다.
- 요청에 `id=N`(1~65535)을 붙이면 응답에 같은 `id`가 실립니다. 붙이지 않으면 응답도
  `id` 없이 옵니다.

## 3. 장치 → 호스트

### `HELLO` — 장치 소개

부팅 직후 한 번, 그리고 `IDENT` 요청마다 보냅니다.

```
HELLO proto=1 fw=0.3.0 board=leonardo keys=8 leds=8 layers=4 uid=A1B2C3D4
```

호스트는 `proto`가 자신이 아는 버전보다 크면 연결을 거부하고 사용자에게 앱 업데이트를
안내합니다. 조용히 계속 진행하지 않습니다.

### `EV` — 입력 이벤트

```
EV t=key  k=<0..7> g=<tap|double|hold|holdend> l=<layer> ms=<millis>
EV t=host tok=<0..255> k=<0..7> l=<layer>
EV t=chord m=<키마스크 hex> l=<layer>
```

- `t=key`는 **알림용**입니다. 장치는 이미 HID를 보냈고, 호스트는 로깅·앱별 레이어 전환·
  LED 반응에 씁니다. 호스트가 안 듣고 있어도 문제 없습니다.
- `t=host`는 **행동 요청**입니다. 장치는 아무 HID도 보내지 않았고, `tok` 토큰에 묶인 호스트
  액션이 실행되기를 기다립니다. 호스트가 없으면 아무 일도 일어나지 않습니다.

### `STATE` — 현재 상태

```
STATE layer=1 bright=64 ledmode=host up=123456
```

### `OK` / `ERR`

```
OK  id=7
ERR id=7 code=<verb|arg|range|crc|long|busy|nomem>
```

### `LOG` — 진단

```
LOG lvl=<d|i|w|e> msg=<base64>
```

기본은 꺼져 있고 `DBG on=1`로 켭니다. 정상 동작 중에는 시리얼을 조용하게 유지합니다.

## 4. 호스트 → 장치

### 연결 관리

| 메시지 | 설명 |
| --- | --- |
| `IDENT` | `HELLO`로 응답 |
| `PING` | `OK`로 응답. 링크 확인용 |
| `STATE?` | `STATE`로 응답 |
| `DBG on=<0\|1>` | `LOG` 출력 토글 |

### LED

| 메시지 | 설명 |
| --- | --- |
| `LED mode=<host\|local>` | 앰비언트 계층 소유권. `host`는 3초 워치독이 걸립니다 |
| `LED bright=<0..255>` | 전역 밝기. 전력 상한 스케일링은 이 뒤에 적용됩니다 |
| `LED i=<0..7> rgb=<RRGGBB> [fx=<효과>] [ms=<주기>]` | 픽셀 하나 |
| `LED all rgb=<RRGGBB> [fx=..] [ms=..]` | 전체 동일 색 |
| `LED frame=<RRGGBB,…8개>` | 전체 프레임 한 번에. 애니메이션은 호스트가 계산 |
| `LED bar=<0..100> rgb=<RRGGBB>` | 진행률 바 (AgentPet `progress`용) |

효과: `solid`, `breathe`, `pulse`, `blink`, `flash`, `rainbow`.

`frame=`은 호스트가 모든 픽셀을 직접 그리는 모드이고, `fx=`는 장치가 애니메이션을 돌리는
모드입니다. **효과를 장치에 맡기면 링크가 잠깐 끊겨도 애니메이션이 끊기지 않습니다.**
호스트 CPU를 아끼려면 `fx=`를, 복잡한 그라디언트가 필요하면 `frame=`을 쓰세요.

### 레이어

```
LAYER set=<0..3>
LAYER base=<0..3>     # momentary 해제 시 돌아갈 기본 레이어
```

### 프로필 전송

프로필 블롭(1016 바이트)을 base64 청크로 나눠 보냅니다. 한 줄 96바이트 제한 때문에 청크당
페이로드는 48바이트(base64 64자)입니다.

```
호스트: PROF begin bytes=1016 crc=<CRC16 hex>
장치:   OK
호스트: PROF data seq=0 b64=<64자>
장치:   OK
        …  (seq 21까지)
호스트: PROF commit
장치:   OK              # CRC 검증 통과, EEPROM 기록 완료
        ERR code=crc    # 검증 실패, EEPROM은 그대로
```

**CRC가 맞을 때까지 EEPROM을 건드리지 않습니다.** 전송 중 케이블이 빠져도 장치에 남아 있던
프로필은 멀쩡합니다. `PROF begin` 후 5초 안에 `commit`이 없으면 스테이징 버퍼를 버립니다.

읽기는 반대 방향입니다.

```
호스트: PROF read
장치:   PROF begin bytes=1016 crc=<hex>
        PROF data seq=0 b64=<…>
        …
        PROF end
```

### 기타

| 메시지 | 설명 |
| --- | --- |
| `SAVE` | 런타임 변경(밝기 등)을 EEPROM에 반영 |
| `RESET defaults=1` | 공장 초기화 |
| `BOOT` | 부트로더 진입 (펌웨어 업데이트용) |

## 5. 연결 수립 절차

호스트가 지켜야 하는 순서입니다.

1. 후보 시리얼 포트를 찾습니다 (VID/PID 또는 사용자 지정).
2. 포트를 열고 **2초 대기**합니다. Leonardo는 포트 열 때 리셋될 수 있습니다.
3. `IDENT`를 보내고 1초 안에 `HELLO`를 기다립니다. 없으면 다음 후보로 넘어갑니다.
4. `proto` 버전을 확인합니다.
5. `PROF read`로 장치 프로필을 읽어 호스트 프로필과 비교합니다. 다르면 사용자에게
   어느 쪽을 기준으로 할지 묻습니다. **말없이 덮어쓰지 않습니다.**
6. `LED mode=host`로 앰비언트 계층을 잡고, 워치독 유지를 위해 최소 1초에 한 번 프레임이나
   `PING`을 보냅니다.

연결이 끊기면 지수 백오프(1s → 2s → 4s → 최대 30s)로 3번 절차부터 재시도합니다.

## 6. 버전 정책

- 필드 **추가**는 마이너 변경입니다. 수신자는 모르는 키를 무시해야 합니다.
- 필드 **삭제**나 의미 변경, `VERB` 삭제는 `proto` 증가입니다.
- 장치는 자신이 아는 유일한 버전만 말합니다. 호환 계층을 장치에 넣지 않습니다.
  2 KB SRAM은 하위 호환 코드를 둘 자리가 아니고, 그 일은 호스트가 합니다.
