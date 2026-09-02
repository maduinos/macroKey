#!/usr/bin/env bash
# Build the firmware image for every registered board.
#
# The images this leaves in firmware/prebuilt/ are what the desktop app writes
# to a keypad, so someone who downloads a release never installs arduino-cli,
# a core, or a library. CI runs this and attaches the results; running it by
# hand is only needed when working on the firmware itself.
#
# Boards come from macrokey/boards.py -- the FQBN, the core, the libraries and
# the artifact name all read from the registry, so adding a board here is
# nothing: register it and this builds it.
#
#   ./tools/build_firmware.sh                 # every board
#   ./tools/build_firmware.sh promicro-rp2040 # one of them
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/firmware/prebuilt"
WORK_DIR="$ROOT_DIR/.build/firmware"
PYTHON_CMD="${PYTHON_CMD:-}"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[31m오류:\033[0m %s\n' "$*" >&2; exit 1; }

detect_python() {
  [[ -n "$PYTHON_CMD" ]] && return
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON_CMD="$candidate"; return; fi
  done
  fail "python3를 찾을 수 없습니다."
}

# arduino-cli is not always on PATH: on this machine it ships inside the
# Arduino IDE AppImage. Look in the obvious places before giving up, and say
# where it was looked for rather than just "not found".
detect_arduino_cli() {
  if [[ -n "${ARDUINO_CLI:-}" ]]; then echo "$ARDUINO_CLI"; return; fi
  if command -v arduino-cli >/dev/null 2>&1; then command -v arduino-cli; return; fi
  for candidate in "$HOME/.local/bin/arduino-cli" "$HOME/bin/arduino-cli"; do
    [[ -x "$candidate" ]] && { echo "$candidate"; return; }
  done
  fail "arduino-cli를 찾을 수 없습니다. PATH에 두거나 ARDUINO_CLI=<경로>로 지정하세요.
      (설치: https://arduino.github.io/arduino-cli/latest/installation/)"
}

detect_python
ARDUINO_CLI_BIN="$(detect_arduino_cli)"
log "arduino-cli: $ARDUINO_CLI_BIN ($("$ARDUINO_CLI_BIN" version | head -1))"

# One line per board: id, fqbn, core, core_url, artifact, libraries (tab-separated).
board_table() {
  "$PYTHON_CMD" - "$@" <<'PY'
import sys
sys.path.insert(0, ".")
from macrokey.boards import BOARDS

wanted = set(sys.argv[1:])
for board in BOARDS:
    if wanted and board.id not in wanted:
        continue
    print("\t".join([
        board.id, board.fqbn, "\x1f".join(board.cores), board.core_url,
        board.build_artifact, board.firmware_name,
        "\x1f".join(board.libraries),
    ]))
PY
}

cd "$ROOT_DIR"
mapfile -t BOARD_ROWS < <(board_table "$@")
[[ ${#BOARD_ROWS[@]} -gt 0 ]] && : || fail "빌드할 보드가 없습니다: $*"

mkdir -p "$OUT_DIR" "$WORK_DIR"

for row in "${BOARD_ROWS[@]}"; do
  IFS=$'\t' read -r board_id fqbn cores core_url artifact image libraries <<<"$row"
  log "$board_id ($fqbn)"

  # Each board gets its own sketchbook. Not tidiness: ~/Arduino/libraries has a
  # `Mouse` declaring architectures=*, which wins over the RP2040 core's own
  # Mouse and breaks that build, while the AVR build needs a Mouse installed.
  # One shared sketchbook cannot satisfy both, so neither board uses one.
  sketchbook="$WORK_DIR/sketchbook-$board_id"
  mkdir -p "$sketchbook"
  export ARDUINO_DIRECTORIES_USER="$sketchbook"

  if [[ -n "$core_url" ]]; then
    "$ARDUINO_CLI_BIN" config add board_manager.additional_urls "$core_url" >/dev/null 2>&1 || true
  fi
  "$ARDUINO_CLI_BIN" core update-index >/dev/null
  IFS=$'\x1f' read -ra core_names <<<"$cores"
  for core in "${core_names[@]}"; do
    "$ARDUINO_CLI_BIN" core install "$core" >/dev/null
  done

  if [[ -n "$libraries" ]]; then
    IFS=$'\x1f' read -ra names <<<"$libraries"
    for name in "${names[@]}"; do
      "$ARDUINO_CLI_BIN" lib install "$name" >/dev/null
    done
  fi

  build_dir="$WORK_DIR/$board_id"
  rm -rf "$build_dir"
  "$ARDUINO_CLI_BIN" compile --fqbn "$fqbn" --output-dir "$build_dir" "$ROOT_DIR/firmware"

  [[ -f "$build_dir/$artifact" ]] || fail "$board_id: 빌드 산출물이 없습니다 ($artifact)"
  cp "$build_dir/$artifact" "$OUT_DIR/$image"
  log "$board_id -> firmware/prebuilt/$image ($(du -h "$OUT_DIR/$image" | cut -f1))"
done

log "완료. firmware/prebuilt/:"
ls -lh "$OUT_DIR"
