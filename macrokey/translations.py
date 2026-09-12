"""Translation tables, keyed by the English source string.

One dict per language. A string missing from a table falls back to the English
it was keyed by, so a partial table is a partial translation rather than a
broken window -- see `i18n.tr`.

Placeholders are named (`{path}`, not `{}`) because word order moves between
languages: Korean puts the verb last, so several of these sentences reorder
their inserts and positional slots would silently swap them.

Keep the keys byte-identical to the source, including the `…` ellipsis and the
`--` and `·` punctuation. A key that does not match exactly is not an error
anywhere; it just never translates.
"""

from __future__ import annotations

KO: dict[str, str] = {
    # -- window, banner, storage ---------------------------------------------
    "Hold any key on its own for 3 seconds to record into it - the pixel turns "
    "red. Hold the same key again to store what you did. After setup you can "
    "quit this app; the pad keeps working as a keyboard.": (
        "아무 키나 단독으로 3초 홀드하면 그 키에 녹음됩니다 — 픽셀이 빨갛게 바뀝니다. "
        "같은 키를 다시 홀드하면 방금 한 동작이 저장됩니다. 설정이 끝나면 이 앱을 종료해도 "
        "됩니다. 패드는 키보드로 계속 동작합니다."
    ),
    "  ● RECORDING - hold the same key again to finish  ": (
        "  ● 녹음 중 - 같은 키를 다시 홀드하면 종료  "
    ),
    "  ● RECORDING key {key} · {gesture} — hold the same key again to finish  ": (
        "  ● 녹음 중 · 키 {key} · {gesture} — 같은 키를 다시 홀드하면 종료  "
    ),
    "Shared keypad macro storage (keyboard + mouse steps).\n"
    "All 16 slots draw from the same 308-record pool.": (
        "키패드 매크로 공용 저장소 (키보드 + 마우스 단계).\n"
        "16개 슬롯이 같은 308레코드 풀을 나눠 씁니다."
    ),
    # -- firmware installation ------------------------------------------------
    "Install firmware?": "펌웨어를 설치할까요?",
    "{board} is plugged in but is not running macroKey firmware.\n\n"
    "Install it now? The keypad will restart and be ready to use.": (
        "{board}이(가) 연결됐지만 macroKey 펌웨어가 올라가 있지 않습니다.\n\n"
        "지금 설치할까요? 설치가 끝나면 키패드가 재시작되고 바로 쓸 수 있습니다."
    ),
    "One step by hand": "한 번은 손이 필요합니다",
    "{board} has never run macroKey firmware, so it has to be put "
    "into its bootloader by hand:\n\n{hint}\n\n"
    "Do that now, then press OK -- the firmware is installed as soon "
    "as the board appears.": (
        "{board}은(는) macroKey 펌웨어를 한 번도 실행한 적이 없어서, 부트로더에 "
        "직접 넣어야 합니다:\n\n{hint}\n\n"
        "지금 그렇게 한 뒤 확인을 누르세요 — 보드가 나타나는 즉시 설치됩니다."
    ),
    "Firmware installed": "펌웨어를 설치했습니다",
    "Firmware install failed": "펌웨어 설치 실패",
    "{board} found, but this build has no firmware for it": (
        "{board}을(를) 찾았지만 이 빌드에는 해당 펌웨어가 없습니다"
    ),
    "Storage {used_pct}% used · {free_pct}% free ({used}/{capacity})": (
        "저장소 {used_pct}% 사용 · {free_pct}% 여유 ({used}/{capacity})"
    ),
    "Not connected": "연결 안 됨",
    "Connected": "연결됨",
    "Connected — profiles still differ. Local edits will not overwrite the "
    "keypad until Sync… is resolved.": (
        "연결됨 — 프로필이 아직 다릅니다. 동기화…를 처리하기 전까지 이 컴퓨터의 편집은 "
        "키패드를 덮어쓰지 않습니다."
    ),
    "Disconnected": "연결 끊김",
    "Keypad disconnected; looking for it again…": "키패드 연결이 끊겼습니다. 다시 찾는 중…",
    "Connect failed": "연결 실패",
    "No keypad found: {detail}": "키패드를 찾을 수 없습니다: {detail}",
    "Auto-connect is disabled": "자동 연결이 꺼져 있습니다",
    "{port} is gone; looking for the keypad": "{port}이(가) 사라졌습니다. 키패드를 찾는 중",
    "no keypad": "키패드 없음",
    " - firmware {firmware}": " - 펌웨어 {firmware}",
    " · profiles differ": " · 프로필 불일치",
    # -- menus ---------------------------------------------------------------
    "Profile": "프로필",
    "Import…": "가져오기…",
    "Export…": "내보내기…",
    "Restore previous version…": "이전 버전 복원…",
    "Help": "도움말",
    "Language": "언어",
    "Mouse macro accuracy": "마우스 매크로 정확도",
    "Recording gestures": "녹음 제스처",
    "Recording setup": "녹음 설정",
    # -- import / export / restore -------------------------------------------
    "Import macroKey profile": "macroKey 프로필 가져오기",
    "Export macroKey profile": "macroKey 프로필 내보내기",
    "macroKey profiles (*.json);;All files (*)": "macroKey 프로필 (*.json);;모든 파일 (*)",
    "Imported {path}": "{path}에서 가져왔습니다",
    "Import failed": "가져오기 실패",
    "Exported profile to {path}": "{path}(으)로 프로필을 내보냈습니다",
    "Export failed": "내보내기 실패",
    "No previous version": "이전 버전 없음",
    "No profile backup exists yet.": "아직 프로필 백업이 없습니다.",
    "Restore previous profile?": "이전 프로필로 복원할까요?",
    "Replace the current local profile with one of the kept copies? "
    "Newest first. The keypad is not changed until you resolve Sync….": (
        "현재 로컬 프로필을 보관된 사본 중 하나로 교체할까요? "
        "최신순입니다. 동기화…를 처리하기 전까지 키패드는 바뀌지 않습니다."
    ),
    "{stamp} — unreadable": "{stamp} — 읽을 수 없음",
    "{stamp} — empty (factory defaults)": "{stamp} — 비어 있음 (공장 기본값)",
    "{stamp} — {macros} recorded macros": "{stamp} — 녹음된 매크로 {macros}개",
    "Restored the previous local profile": "이전 로컬 프로필을 복원했습니다",
    "Restore failed": "복원 실패",
    "Could not save profile": "프로필을 저장할 수 없습니다",
    " — use Sync… to update the keypad": " — 키패드에 반영하려면 동기화…를 쓰세요",
    # -- help texts ----------------------------------------------------------
    "Default mouse replay is relative: clicks happen at the current pointer, "
    "and movement starts there. This is the reliable choice.\n\n"
    "The keypad replays a recorded movement over the time it was recorded "
    "over, rather than as one jump, so pointer acceleration affects the "
    "replay the same way it affected your hand. Flat acceleration removes "
    "the variable altogether -- macroKey will offer that next.\n\n"
    "Fixed position is experimental. It homes to the top-left and depends on "
    "the same monitor layout, scaling, pointer speed/acceleration, window "
    "positions, and application state. Test fixed-position macros on a safe "
    "target before assigning them to destructive actions.\n\nClicking the "
    "Windows taskbar is the trap worth naming. Windows 11 centres taskbar "
    "icons by default, so every icon moves whenever the number of open apps "
    "changes -- opening macroKey itself is enough to shift them. A macro aimed "
    "at one then lands on its neighbour. Set Settings > Personalisation > "
    "Taskbar > Taskbar behaviours > Taskbar alignment to Left and pin what you "
    "aim at: pinned icons keep their place and new windows are added to their "
    "right.": (
        "마우스 재생의 기본은 상대 방식입니다. 클릭은 현재 포인터 위치에서 일어나고 이동도 "
        "거기서 시작합니다. 이쪽이 믿을 만한 선택입니다.\n\n"
        "키패드는 녹화된 이동을 한 번에 점프시키지 않고 녹화에 걸린 시간에 맞춰 재생합니다. "
        "그래서 포인터 가속이 손으로 움직였을 때와 같은 방식으로 재생에도 적용됩니다. 가속을 "
        "flat으로 두면 이 변수가 아예 사라집니다 — macroKey가 이어서 그걸 제안합니다.\n\n"
        "위치 고정은 실험 기능입니다. 좌측 상단으로 포인터를 옮긴 뒤 동작하며, 모니터 배치·"
        "배율·포인터 속도/가속·창 위치·프로그램 상태가 모두 같아야 합니다. 되돌릴 수 없는 "
        "동작에 붙이기 전에 안전한 대상에서 먼저 시험하세요.\n\n"
        "특히 짚어둘 함정은 Windows 작업 표시줄 클릭입니다. Windows 11은 작업 표시줄 "
        "아이콘을 기본으로 가운데 정렬하므로, 열려 있는 앱 개수가 바뀔 때마다 아이콘이 "
        "전부 움직입니다 — macroKey를 켜는 것만으로도 밀립니다. 그러면 한 아이콘을 "
        "겨냥한 매크로가 옆 아이콘을 누릅니다. 설정 > 개인 설정 > 작업 표시줄 > 작업 "
        "표시줄 동작 > 작업 표시줄 맞춤을 왼쪽으로 바꾸고 겨냥할 앱을 고정하세요. 고정된 "
        "아이콘은 자리를 지키고 새 창은 그 오른쪽에 붙습니다."
    ),
    "Tap slot: hold a key by itself for 3 seconds.\n\n"
    "Double slot: tap, then press and hold the same key within 250 ms; keep "
    "holding for 3 seconds.\n\n"
    "The pixel turns red while all keyboard input is being captured. Hold the "
    "same key again to save, or use Discard recording in this window.": (
        "탭 슬롯: 키 하나를 단독으로 3초 홀드합니다.\n\n"
        "더블 슬롯: 한 번 탭한 뒤 250 ms 안에 같은 키를 다시 눌러 3초 홀드합니다.\n\n"
        "키보드 입력을 캡처하는 동안 픽셀이 빨갛게 유지됩니다. 같은 키를 다시 홀드하면 "
        "저장되고, 이 창의 '녹음 버리기'로 취소할 수 있습니다."
    ),
    # -- toolbar -------------------------------------------------------------
    "Port": "포트",
    "Leave as Auto to use whichever board identifies itself as a keypad.": (
        "Auto로 두면 키패드로 식별되는 보드를 자동으로 씁니다."
    ),
    "Connect": "연결",
    "Connecting...": "연결 중...",
    "Disconnect": "연결 해제",
    "Sync…": "동기화…",
    "Sync needed": "동기화 필요",
    "Resolve which profile wins when this computer and the keypad differ.": (
        "이 컴퓨터와 키패드의 프로필이 다를 때 어느 쪽을 쓸지 정합니다."
    ),
    "Brightness": "밝기",
    "Brightness {value}": "밝기 {value}",
    "Typing": "타이핑",
    "Typing speed {value} ms per character": "타이핑 속도 문자당 {value} ms",
    "Typing speed {value} ms per character (saving…)": (
        "타이핑 속도 문자당 {value} ms (저장 중…)"
    ),
    "Pause between characters when the pad replays typed text.\n"
    "5 ms is what the pad does out of the box. Drop it to 1 ms for the\n"
    "fastest replay, or raise it if the receiving window misses the start.": (
        "패드가 입력한 텍스트를 재생할 때 문자 사이의 간격입니다.\n"
        "패드 기본값은 5 ms입니다. 가장 빠르게 하려면 1 ms로 낮추고,\n"
        "받는 창이 앞부분을 놓치면 값을 올리세요."
    ),
    "Clears every binding and every recorded macro -- on this computer\n"
    "and on the keypad -- and puts the hyper + 1..8 defaults back.": (
        "이 컴퓨터와 키패드 양쪽에서 모든 바인딩과 녹음된 매크로를 지우고\n"
        "hyper + 1..8 기본값으로 되돌립니다."
    ),
    # -- keys / LED ----------------------------------------------------------
    "Key": "키",
    "Key {key} {gesture}": "키 {key} {gesture}",
    "Key {key} {gesture} - {where}": "키 {key} {gesture} - {where}",
    "LED": "LED",
    "Resting LED colour": "대기 LED 색",
    "Resting LED #{value}": "대기 LED #{value}",
    "Colour the pad rests at when nothing is happening.": (
        "아무 일도 없을 때 패드가 유지하는 색입니다."
    ),
    "off": "꺼짐",
    "Preview unavailable: {detail}": "미리보기를 쓸 수 없습니다: {detail}",
    "Preview stopped: {detail}": "미리보기가 중지되었습니다: {detail}",
    # -- recording panel -----------------------------------------------------
    "Recording": "녹음",
    "Setup": "설정",
    "Check or retry permission setup for global keyboard and mouse recording.": (
        "전역 키보드·마우스 녹음 권한 설정을 확인하거나 다시 시도합니다."
    ),
    "Include mouse": "마우스 포함",
    "Clicks, wheel, and pointer movement. By default clicks happen at the "
    "current pointer and movement is relative to it.": (
        "클릭·휠·포인터 이동을 포함합니다. 기본적으로 클릭은 현재 포인터 위치에서 "
        "일어나고 이동은 그 위치를 기준으로 합니다."
    ),
    "Fixed screen": "화면 고정",
    "Homes the pointer before recording and replay. Fixed clicks are only "
    "repeatable with the same monitor layout, scaling, pointer speed, and "
    "window positions. Relative/current-pointer replay is safer.": (
        "녹음과 재생 전에 포인터를 기준 위치로 옮깁니다. 고정 클릭은 모니터 배치·배율·"
        "포인터 속도·창 위치가 모두 같을 때만 재현됩니다. 상대/현재 포인터 재생이 더 "
        "안전합니다."
    ),
    "Real timing": "실제 타이밍",
    "Record how long each key stays down (press, wait, release) instead of\n"
    "turning every keystroke into a tap. Use this for game holds like\n"
    "Shift+W. Ordinary shortcuts and typing macros usually leave this off.\n"
    "Long holds use several records (delays max out at 2550 ms each).": (
        "키를 탭으로 줄이지 않고 누른 시간 그대로(press → 대기 → release) 녹음합니다.\n"
        "Shift+W처럼 게임에서 길게 누르는 동작에 씁니다. 일반 단축키·타이핑 매크로는\n"
        "보통 끕니다. 긴 홀드는 레코드를 여러 개 씁니다(delay는 스텝당 최대 2550 ms)."
    ),
    "Discard recording": "녹음 버리기",
    "Stop global capture and discard everything recorded this time.": (
        "전역 캡처를 멈추고 이번에 녹음한 내용을 모두 버립니다."
    ),
    "Recording discarded": "녹음을 버렸습니다",
    "Hold a pad key for 3 seconds to record. Captured steps appear here.": (
        "패드 키를 3초 홀드하면 녹음이 시작됩니다. 캡처된 단계가 여기에 표시됩니다."
    ),
    "(listening…)": "(대기 중…)",
    "Recording into key {key} ({gesture}) - hold it again to finish": (
        "키 {key}({gesture})에 녹음 중 - 다시 홀드하면 종료"
    ),
    "Recording key {key} · {gesture}": "녹음 중 · 키 {key} · {gesture}",
    "! {count} step(s) removed: looked like a password": (
        "! {count}개 단계를 제거했습니다: 비밀번호로 보였습니다"
    ),
    "nothing was captured": "캡처된 것이 없습니다",
    # -- reset ---------------------------------------------------------------
    "Reset everything?": "전부 초기화할까요?",
    "Every key binding and every recorded macro is cleared -- on this computer "
    "and on the keypad -- and the hyper + 1..8 defaults go back.\n\n"
    "The previous local profile remains available under Profile > Restore "
    "previous version.": (
        "이 컴퓨터와 키패드 양쪽에서 모든 키 바인딩과 녹음된 매크로가 지워지고 "
        "hyper + 1..8 기본값으로 돌아갑니다.\n\n"
        "직전의 로컬 프로필은 프로필 > 이전 버전 복원에서 계속 꺼낼 수 있습니다."
    ),
    "Reset": "초기화",
    "Cancel": "취소",
    "Reset cancelled": "초기화를 취소했습니다",
    "Reset failed": "초기화 실패",
    "Finish or discard the recording before resetting": (
        "초기화하기 전에 녹음을 끝내거나 버리세요"
    ),
    "Resetting the keypad…": "키패드를 초기화하는 중…",
    "Reset. The keypad and this computer are back to defaults.": (
        "초기화했습니다. 키패드와 이 컴퓨터가 모두 기본값으로 돌아갔습니다."
    ),
    "Reset this computer. The keypad still holds its own bindings until you "
    "connect and choose Push in Sync….": (
        "이 컴퓨터를 초기화했습니다. 연결한 뒤 동기화…에서 '키패드로 보내기'를 고르기 "
        "전까지 키패드는 자기 바인딩을 그대로 갖고 있습니다."
    ),
    "The keypad kept its profile: {message}": "키패드는 프로필을 유지했습니다: {message}",
    "The keypad was cleared, but could not save: {detail}": (
        "키패드는 지워졌지만 저장하지 못했습니다: {detail}"
    ),
    # -- sync ----------------------------------------------------------------
    "Profile differs": "프로필 불일치",
    "The keypad lost its profile": "키패드가 프로필을 잃었습니다",
    "The keypad is holding factory defaults -- no recorded macros and the "
    "plain hyper + 1..8 bindings. This computer still has yours.\n\n"
    "Push: put this computer's profile back on the keypad.\n"
    "Pull: accept the empty one, losing what is on this computer.\n"
    "Cancel: leave both as they are.\n\n"
    "Profile > Restore previous version keeps earlier copies if this "
    "computer's profile is already the empty one.": (
        "키패드가 공장 기본값을 들고 있습니다 — 녹음된 매크로가 없고 바인딩도 "
        "기본 hyper + 1..8입니다. 이 컴퓨터에는 아직 매드님 프로필이 남아 있습니다.\n\n"
        "보내기: 이 컴퓨터의 프로필을 키패드에 다시 넣습니다.\n"
        "가져오기: 비어 있는 쪽을 받아들이고, 이 컴퓨터의 내용을 잃습니다.\n"
        "취소: 양쪽 모두 그대로 둡니다.\n\n"
        "이 컴퓨터의 프로필마저 이미 비어 있다면, 프로필 > 이전 버전 복원에 "
        "그 전 사본들이 남아 있습니다."
    ),
    "This computer and the keypad have different profiles.\n\n"
    "Pull: use what is on the keypad.\n"
    "Push: overwrite the keypad with this computer's profile.\n"
    "Cancel: leave both as they are.": (
        "이 컴퓨터와 키패드의 프로필이 다릅니다.\n\n"
        "가져오기: 키패드에 있는 것을 씁니다.\n"
        "보내기: 이 컴퓨터의 프로필로 키패드를 덮어씁니다.\n"
        "취소: 양쪽 모두 그대로 둡니다."
    ),
    "Pull from keypad": "키패드에서 가져오기",
    "Push to keypad": "키패드로 보내기",
    "Finish or discard the recording before synchronizing profiles": (
        "프로필을 동기화하기 전에 녹음을 끝내거나 버리세요"
    ),
    "Reading the keypad profile…": "키패드 프로필을 읽는 중…",
    "Writing this computer's profile to the keypad…": (
        "이 컴퓨터의 프로필을 키패드에 쓰는 중…"
    ),
    "Adopted the keypad profile": "키패드 프로필을 적용했습니다",
    "Keypad updated from this computer": "이 컴퓨터의 내용으로 키패드를 갱신했습니다",
    "Sync failed": "동기화 실패",
    "Read succeeded, but the profile could not be saved: {detail}": (
        "읽기는 성공했지만 프로필을 저장하지 못했습니다: {detail}"
    ),
    # -- saving --------------------------------------------------------------
    "{what} saved; writing to the keypad…": "{what} 저장됨. 키패드에 쓰는 중…",
    "{what} saved. It reaches the keypad on the next connect.": (
        "{what} 저장됨. 다음 연결 때 키패드에 반영됩니다."
    ),
    "{what} saved locally. The current device operation will finish first.": (
        "{what}을(를) 로컬에 저장했습니다. 진행 중인 장치 작업이 먼저 끝납니다."
    ),
    "{what} saved locally. Profiles still differ; use Sync… to choose a side.": (
        "{what}을(를) 로컬에 저장했습니다. 프로필이 아직 다릅니다. 동기화…에서 한쪽을 "
        "고르세요."
    ),
    "Done - {what} written to the keypad": "완료 - {what}을(를) 키패드에 썼습니다",
    "{what} saved, but the device write failed: {detail}": (
        "{what}은(는) 저장됐지만 장치 쓰기에 실패했습니다: {detail}"
    ),
    "Could not save: {detail}": "저장할 수 없습니다: {detail}",
    "Could not build the keypad profile: {detail}": (
        "키패드 프로필을 만들 수 없습니다: {detail}"
    ),
    "Could not update the keypad: {detail}": "키패드를 갱신할 수 없습니다: {detail}",
    # -- capture setup -------------------------------------------------------
    "Recording cannot see the keyboard on this session.": (
        "이 세션에서는 녹음이 키보드를 볼 수 없습니다."
    ),
    "Enable recording?": "녹음을 활성화할까요?",
    "{detail}\n\nAllow macroKey to set this up? You will be asked for your "
    "administrator password once. This grants your account access to all "
    "keyboard and mouse input, including passwords; macroKey opens that input "
    "only while the pixel and banner show recording. The keypad still works "
    "either way — only recording needs this.": (
        "{detail}\n\nmacroKey가 설정하도록 허용할까요? 관리자 비밀번호를 한 번 묻습니다. "
        "이 권한은 비밀번호를 포함한 모든 키보드·마우스 입력에 계정이 접근할 수 있게 "
        "합니다. macroKey는 픽셀과 배너가 녹음 중임을 표시하는 동안에만 입력을 엽니다. "
        "키패드 자체는 어느 쪽이든 동작합니다 — 녹음에만 필요합니다."
    ),
    "Preparing recording support…": "녹음 지원을 준비하는 중…",
    "Recording setup skipped — hold-to-record will not capture": (
        "녹음 설정을 건너뛰었습니다 — 홀드 녹음이 캡처하지 못합니다"
    ),
    "Recording is ready": "녹음 준비 완료",
    "Recording ready": "녹음 준비됨",
    "Hold a key for 3 seconds to record. If a brand-new keyboard appears after "
    "reboot and recording fails again, log out and back in once so the input "
    "group applies.": (
        "키를 3초 홀드하면 녹음됩니다. 재부팅 후 새 키보드가 인식되면서 녹음이 다시 "
        "실패하면, input 그룹이 적용되도록 로그아웃했다가 다시 로그인하세요."
    ),
    "Could not finish setup": "설정을 마치지 못했습니다",
    "{message}\n\nYou can retry next launch, or run:\n"
    "  sudo usermod -aG input $USER\nthen log out and back in.": (
        "{message}\n\n다음 실행에서 다시 시도하거나 아래를 실행하세요:\n"
        "  sudo usermod -aG input $USER\n그 뒤 로그아웃했다가 다시 로그인하세요."
    ),
    # -- pointer acceleration ------------------------------------------------
    "Pointer acceleration": "포인터 가속",
    "This desktop does not expose a pointer acceleration setting that macroKey "
    "can read, so there is nothing to change here.": (
        "이 데스크톱은 macroKey가 읽을 수 있는 포인터 가속 설정을 제공하지 않아서 여기서 "
        "바꿀 것이 없습니다."
    ),
    "Pointer acceleration is already flat, which is the setting mouse macros "
    "replay most accurately under.": (
        "포인터 가속이 이미 flat입니다. 마우스 매크로가 가장 정확하게 재생되는 설정입니다."
    ),
    "Your desktop scales pointer movement by how fast it is (acceleration "
    "profile: {profile}).\n\nThe keypad replays a movement at the speed it "
    "was recorded, which cancels most of that. It does not cancel all of it: a "
    "fast movement replays at the limit of what USB carries, so any delay "
    "stretches the gesture and the curve then multiplies it by less, which can "
    "put the macro short of where you drew it. Turning acceleration off "
    "removes the variable entirely.\n\nSwitch to flat pointer acceleration? "
    "It changes how the "
    "mouse feels everywhere, not just in macros. To undo it later:\n\n"
    "{undo}": (
        "이 데스크톱은 포인터 이동 속도에 따라 이동 거리를 조절합니다 (가속 프로필: "
        "{profile}).\n\n키패드는 녹화된 이동을 녹화 당시 속도로 재생하므로 이 효과가 대부분 "
        "상쇄됩니다. 다만 전부는 아닙니다 — 빠른 이동은 USB가 실어나를 수 있는 한계 속도로 "
        "재생되므로, 리포트가 조금만 늦어도 제스처가 늘어지고 가속 곡선은 그만큼 덜 곱합니다. "
        "그러면 매크로가 그린 곳보다 짧게 떨어질 수 있습니다. 가속을 끄면 이 변수가 "
        "완전히 사라집니다.\n\n"
        "포인터 가속을 flat으로 바꿀까요? 매크로뿐 아니라 평소 마우스 감각도 함께 바뀝니다. "
        "나중에 되돌리려면:\n\n{undo}"
    ),
    "The recording you just made moves the pointer faster than the keypad can "
    "replay a count at a time, so this desktop's acceleration has a say in how "
    "far it goes.\n\n": (
        "방금 녹화한 이동이 키패드가 한 카운트씩 재생할 수 있는 속도보다 빠릅니다. "
        "그래서 이 데스크톱의 가속이 이동 거리에 관여하게 됩니다.\n\n"
    ),
    # -- about ----------------------------------------------------------------
    "About macroKey": "macroKey 정보",
    "Created by maduinos": "만든 곳: maduinos",
    # -- what an update did ---------------------------------------------------
    "What changed:": "바뀐 내용:",
    "Keypad firmware updated": "키패드 펌웨어를 업데이트했습니다",
    "an older version": "이전 버전",
    "The keypad was running firmware {old} and this build carries {new}, so it "
    "was written to the pad. Your macros were not touched.\n\nFirmware comes "
    "with the app and follows it, so this happens whenever the app moves "
    "ahead. What changed is in the app's release notes.": (
        "키패드가 펌웨어 {old}을(를) 쓰고 있었고 이 빌드는 {new}을(를) 갖고 있어서 "
        "패드에 새로 썼습니다. 매크로는 그대로입니다.\n\n펌웨어는 앱에 함께 들어 있고 "
        "앱을 따라가므로, 앱이 올라갈 때마다 이 일이 일어납니다. 무엇이 바뀌었는지는 "
        "앱 릴리스 노트에 있습니다."
    ),
    "Left pointer acceleration alone. Help > Mouse macro accuracy offers it "
    "again.": (
        "포인터 가속을 그대로 두었습니다. 도움말 > 마우스 매크로 정확도에서 다시 제안합니다."
    ),
    "pointer acceleration is now flat": "포인터 가속을 flat으로 바꿨습니다",
    "could not change the pointer acceleration setting": (
        "포인터 가속 설정을 바꿀 수 없었습니다"
    ),
    "the pointer acceleration setting did not take": (
        "포인터 가속 설정이 적용되지 않았습니다"
    ),
    # -- language ------------------------------------------------------------
    "System default": "시스템 기본값",
    "Restart required": "재시작 필요",
    "The language changes the next time macroKey starts.": (
        "언어는 macroKey를 다시 시작할 때 바뀝니다."
    ),
    # -- slot dialog ---------------------------------------------------------
    "Key {key} · {gesture}": "키 {key} · {gesture}",
    "Now: {description}": "현재: {description}",
    "Press keys": "키 입력받기",
    "Stop": "중지",
    "Fills the field from the next combination pressed on the keyboard. The "
    "field can also just be typed into.": (
        "키보드에서 다음에 누르는 조합으로 칸을 채웁니다. 칸에 직접 입력해도 됩니다."
    ),
    "Set": "적용",
    "Send a shortcut": "단축키 보내기",
    "Press keys reads the keyboard itself, so every key arrives as the key it "
    "is. The field can also just be typed into.": (
        "키 입력받기는 키보드를 직접 읽으므로, 어떤 키든 누른 그대로 들어옵니다. 칸에 "
        "직접 입력해도 됩니다."
    ),
    "Listening. The combination is taken here, so it does not also do whatever "
    "it is bound to today.": (
        "듣는 중입니다. 누른 조합을 여기서 가져가므로, 지금 그 조합에 걸려 있는 동작은 "
        "실행되지 않습니다."
    ),
    "Listening. These keys reach the desktop too, so a combination it owns "
    "will act on it while you press it.": (
        "듣는 중입니다. 누른 키가 데스크톱에도 전달되므로, 데스크톱이 쓰는 조합이면 "
        "누르는 동안 그 동작도 실행됩니다."
    ),
    "Reading this window only ({reason}). A key the desktop takes first never "
    "arrives here; type that one into the field.": (
        "이 창에서만 받는 중입니다 ({reason}). 데스크톱이 먼저 가져가는 키는 여기까지 "
        "오지 않으니, 그런 키는 칸에 직접 입력하세요."
    ),
    "a recording is running": "녹음이 진행 중입니다",
    "Read {value}, but this keypad cannot send it: {reason}": (
        "{value}을(를) 읽었지만 이 키패드는 보낼 수 없습니다: {reason}"
    ),
    "no keyboard among the readable input devices": (
        "읽을 수 있는 입력 장치 중에 키보드가 없습니다"
    ),
    "Stopped listening -- no key was pressed.": (
        "아무 키도 누르지 않아 듣기를 멈췄습니다."
    ),
    "Clear {gesture} binding": "{gesture} 바인딩 지우기",
    "That is not a shortcut this keypad can send": (
        "이 키패드가 보낼 수 없는 단축키입니다"
    ),
    # -- gesture names -------------------------------------------------------
    # Lowercase forms are inserted into sentences; the capitalised ones are the
    # two column headers over the key grid.
    "tap": "탭",
    "double": "더블",
    "Tap": "탭",
    "Double": "더블",
    # -- repeating a recording -----------------------------------------------
    # 횟수와 그 횟수가 실제로 몇 분인지를 같이 보여 줍니다. 255는 숫자일 뿐이고,
    # 결정을 내리게 하는 쪽은 시간입니다.
    "Repeat the recording": "녹음 반복",
    "How many times one press replays the recording. The pad stops a repeat early "
    "when any of its keys is pressed.": "한 번 눌렀을 때 녹음을 몇 번 재생할지입니다. "
    "반복 중에 패드의 아무 키나 누르면 멈춥니다.",
    ", repeated {repeat} times ({duration})": ", {repeat}회 반복 ({duration})",
    "once": "1회",
    "Bind a shortcut or record into this key first.": "먼저 단축키를 넣거나 녹음하세요.",
    "That repeat will not fit": "반복을 넣을 자리가 없습니다",
    "about {seconds} seconds": "약 {seconds}초",
    "about {minutes} minutes": "약 {minutes}분",
    "about {hours} hours": "약 {hours}시간",
    # -- widgets -------------------------------------------------------------
    # `AUTO_PORT` ("Auto") is deliberately absent: it is compared against the
    # combo box text in `_chosen_port`, so it is a sentinel before it is a word.
    "Press the shortcut...": "단축키를 누르세요...",
    # -- binding descriptions ------------------------------------------------
    "nothing": "없음",
    "(nothing)": "(없음)",
    "empty": "비어 있음",
    "recording, {detail} (on the keypad)": "녹음됨, {detail} (키패드에 저장)",
    "{typed} characters": "{typed}자",
    "{others} key": "{others}개 동작",
    "{others} keys": "{others}개 동작",
    "Nothing was captured. Hold a pad key for 3 seconds, do the thing, hold "
    "again to finish.": (
        "캡처된 것이 없습니다. 패드 키를 3초 홀드하고, 할 동작을 한 뒤, 다시 홀드해 "
        "종료하세요."
    ),
    "Nothing was captured. On Wayland, prefer being in the `input` group so "
    "capture uses evdev (every window). Without it, only X11 windows are "
    "visible to the fallback recorder.": (
        "캡처된 것이 없습니다. Wayland에서는 `input` 그룹에 속해야 evdev로 캡처해 모든 창을 "
        "볼 수 있습니다. 그렇지 않으면 대체 녹음기에는 X11 창만 보입니다."
    ),
    "Nothing was captured. {reason}": "캡처된 것이 없습니다. {reason}",
    # -- session -------------------------------------------------------------
    "Recording into key {key} ({gesture}). Hold it again to finish.": (
        "키 {key}({gesture})에 녹음 중입니다. 다시 홀드하면 종료됩니다."
    ),
    "Already recording into key {key}. Hold key {key} again to finish.": (
        "이미 키 {key}에 녹음 중입니다. 키 {key}을(를) 다시 홀드하면 종료됩니다."
    ),
    "Cannot record: {detail}": "녹음할 수 없습니다: {detail}",
    "Recording ran for {minutes} minutes; storing it now": (
        "녹음이 {minutes}분 동안 진행되어 지금 저장합니다"
    ),
    "Could not store the recording: {detail}": "녹음을 저장할 수 없습니다: {detail}",
    "Could not stop recording: {detail}": "녹음을 멈출 수 없습니다: {detail}",
    "Recorded, but could not write it to the keypad: {detail}": (
        "녹음했지만 키패드에 쓰지 못했습니다: {detail}"
    ),
    "Key {key} ({gesture}): {where}": "키 {key}({gesture}): {where}",
    "{hint} Key {key} ({gesture}) is unchanged.": (
        "{hint} 키 {key}({gesture})은(는) 그대로입니다."
    ),
    # -- updates -------------------------------------------------------------
    "Check for updates": "업데이트 확인",
    "Update the app automatically": "앱 자동 업데이트",
    "Update keypad firmware automatically": "키패드 펌웨어 자동 업데이트",
    "Connect the keypad to check its firmware": (
        "펌웨어를 확인하려면 키패드를 연결하세요"
    ),
    "No app update from here: {detail}": "여기서는 앱을 업데이트할 수 없습니다: {detail}",
    "Could not update the app: {detail}": "앱을 업데이트하지 못했습니다: {detail}",
    "macroKey v{version} is the newest release": (
        "macroKey v{version}이(가) 최신 릴리스입니다"
    ),
    "macroKey v{version} installed - restart to use it": (
        "macroKey v{version}을(를) 설치했습니다 - 다시 실행하면 적용됩니다"
    ),
    "Update installed": "업데이트 설치됨",
    "macroKey v{version} has been downloaded and installed.\n\n"
    "Close and reopen the app to start using it. The keypad keeps "
    "working as a keyboard either way.": (
        "macroKey v{version}을(를) 내려받아 설치했습니다.\n\n"
        "앱을 닫았다 다시 열면 새 버전으로 실행됩니다. 그 사이에도 키패드는 "
        "키보드로 계속 동작합니다."
    ),
    "Updating keypad firmware {old} to {new}": (
        "키패드 펌웨어를 {old}에서 {new}(으)로 업데이트하는 중"
    ),
    "Firmware update failed": "펌웨어 업데이트 실패",
    "Keypad firmware updated to {version}": "키패드 펌웨어를 {version}(으)로 업데이트했습니다",
    "Keypad firmware was already current": "키패드 펌웨어는 이미 최신이었습니다",
    "Update the keypad?": "키패드를 업데이트할까요?",
    "The keypad is running firmware {old}; this app has {new}.\n\n"
    "Update it now? It takes a few seconds and the keypad restarts.": (
        "키패드는 펌웨어 {old}을(를) 실행 중이고 이 앱에는 {new}이(가) 있습니다.\n\n"
        "지금 업데이트할까요? 몇 초 걸리고 키패드가 다시 시작됩니다."
    ),
}

#: Every language the UI can be set to, by code. English is absent on purpose:
#: it is the source, so `tr` returning its argument already is English.
TRANSLATIONS: dict[str, dict[str, str]] = {"ko": KO}
