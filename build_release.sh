#!/usr/bin/env bash
# Build a standalone macroKey GUI binary with PyInstaller.
#
#   Ubuntu / Linux  : ./build_release.sh  -> releases/linux/macrokey
#   Windows Git Bash: ./build_release.sh  -> releases/windows/macrokey.exe
#
# Pattern matches 28_FPGA_Signal_Analyzer/build_release.sh (GUI onefile).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRY_SCRIPT="main.py"
APP_NAME="macrokey"
ASSETS_DIR="$ROOT_DIR/assets"
ICON_PNG="$ASSETS_DIR/app_icon.png"
ICON_ICO="$ASSETS_DIR/app_icon.ico"
RELEASE_DIR="$ROOT_DIR/releases"
LINUX_RELEASE_DIR="$RELEASE_DIR/linux"
WINDOWS_RELEASE_DIR="$RELEASE_DIR/windows"
BUILD_ROOT="$ROOT_DIR/.build"
PYINSTALLER_HOOKS_DIR="$ROOT_DIR/tools/pyinstaller_hooks"
# Built firmware images, one per board, that the app writes to a keypad. Bundled
# so nobody who downloads a release has to install arduino-cli to flash a board
# they just soldered. ./tools/build_firmware.sh puts them here; CI builds them
# in their own job and downloads them before this runs.
FIRMWARE_PREBUILT_DIR="$ROOT_DIR/firmware/prebuilt"
CURRENT_OS="$(uname -s)"
PYTHON_CMD=""
MIN_PYTHON_VERSION="3.10"
DEPS_DIR=""
# Same directory as DEPS_DIR, spelled the way the Python we invoke understands.
# Git Bash hands us POSIX paths (/c/Users/...) but runs a native Windows Python,
# and MSYS only rewrites command arguments -- never environment variables. So
# PYTHONPATH has to carry the native spelling or the deps are simply not there.
DEPS_DIR_NATIVE=""
RUN_TESTS=1

# Heavy packages never imported by this app. Excluding them keeps the onefile
# binary small when they happen to sit in the same env.
EXCLUDE_MODULES=(
  IPython
  OpenGL
  PIL
  cv2
  h5py
  jupyterlab
  matplotlib
  notebook
  numba
  onnxruntime
  pandas
  pygame
  pytest
  scipy
  sklearn
  sympy
  tensorflow
  torch
  torchvision
  tkinter
)

# pyserial's list_ports imports every platform backend at module scope.
LINUX_EXCLUDE_MODULES=(
  serial.tools.list_ports_osx
  serial.tools.list_ports_windows
)
WINDOWS_EXCLUDE_MODULES=(
  serial.tools.list_ports_linux
  serial.tools.list_ports_osx
  serial.tools.list_ports_posix
)

export PYGAME_HIDE_SUPPORT_PROMPT=1
XCB_CURSOR_LIB="${XCB_CURSOR_LIB:-}"

log() {
  printf '[build] %s\n' "$1"
}

fail() {
  printf '[error] %s\n' "$1" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: ./build_release.sh [--skip-tests] [-h|--help]

  --skip-tests   Do not run pytest before building.
USAGE
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-tests) RUN_TESTS=0 ;;
      -h|--help) usage; exit 0 ;;
      *) usage >&2; fail "알 수 없는 옵션: $1" ;;
    esac
    shift
  done
}

detect_python() {
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
  else
    fail "Python 실행 파일을 찾을 수 없습니다. (python3 또는 python 필요)"
  fi

  "$PYTHON_CMD" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    || fail "Python $MIN_PYTHON_VERSION 이상이 필요합니다. 현재: $("$PYTHON_CMD" --version 2>&1)"

  local python_series
  local path_separator
  python_series="$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  path_separator="$("$PYTHON_CMD" -c 'import os; print(os.pathsep)')"
  DEPS_DIR="$ROOT_DIR/.build-deps/python$python_series"
  DEPS_DIR_NATIVE="$DEPS_DIR"
  if command -v cygpath >/dev/null 2>&1; then
    DEPS_DIR_NATIVE="$(cygpath -w "$DEPS_DIR")"
  fi
  export PYTHONPATH="$DEPS_DIR_NATIVE${PYTHONPATH:+$path_separator$PYTHONPATH}"

  log "Python: $("$PYTHON_CMD" --version 2>&1)"
  log "의존성 경로: $DEPS_DIR_NATIVE"
}

ensure_deps() {
  log "의존성 확인 (requirements.txt)"
  mkdir -p "$DEPS_DIR"
  "$PYTHON_CMD" -m pip install -q --upgrade --target "$DEPS_DIR_NATIVE" \
    -r "$ROOT_DIR/requirements.txt"
  "$PYTHON_CMD" - <<'PY' || fail "런타임 의존성 import 실패"
import importlib.util
import sys

need = ["PySide6", "serial", "pynput"]
if sys.platform.startswith("linux"):
    need.append("evdev")
missing = [name for name in need if importlib.util.find_spec(name) is None]
if missing:
    print("missing: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
PY
  "$PYTHON_CMD" -m PyInstaller --version >/dev/null 2>&1 \
    || fail "PyInstaller가 없습니다. requirements.txt에 포함돼 있으니 pip가 실패했는지 확인하세요."
}

run_tests() {
  log "ruff 실행"
  (cd "$ROOT_DIR" && "$PYTHON_CMD" -m ruff check .) \
    || fail "정적 검사 실패. 수정 후 다시 빌드하세요."
  log "pytest 실행"
  if [[ "$CURRENT_OS" == Linux* ]]; then
    (cd "$ROOT_DIR" && QT_QPA_PLATFORM=offscreen "$PYTHON_CMD" -m pytest -q) \
      || fail "테스트 실패. 수정 후 다시 빌드하세요. (건너뛰려면 --skip-tests)"
  else
    (cd "$ROOT_DIR" && "$PYTHON_CMD" -m pytest -q) \
      || fail "테스트 실패. 수정 후 다시 빌드하세요. (건너뛰려면 --skip-tests)"
  fi
}

prepare_dirs() {
  rm -rf "$BUILD_ROOT"
  mkdir -p "$BUILD_ROOT/linux/dist" "$BUILD_ROOT/linux/build" "$BUILD_ROOT/linux/spec"
  mkdir -p "$BUILD_ROOT/windows/dist" "$BUILD_ROOT/windows/build" "$BUILD_ROOT/windows/spec"
  mkdir -p "$LINUX_RELEASE_DIR" "$WINDOWS_RELEASE_DIR"
}

find_xcb_cursor_library() {
  local candidate

  if [[ -n "$XCB_CURSOR_LIB" ]]; then
    [[ -f "$XCB_CURSOR_LIB" ]] || fail "libxcb-cursor.so.0 경로가 올바르지 않습니다: $XCB_CURSOR_LIB"
    printf '%s\n' "$XCB_CURSOR_LIB"
    return
  fi

  if command -v ldconfig >/dev/null 2>&1; then
    candidate="$(ldconfig -p 2>/dev/null | awk '/libxcb-cursor\.so\.0/{print $NF; exit}')"
    if [[ -n "$candidate" && -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  fi

  for candidate in \
    /usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0 \
    /usr/lib64/libxcb-cursor.so.0 \
    /usr/lib/libxcb-cursor.so.0 \
    /lib/x86_64-linux-gnu/libxcb-cursor.so.0 \
    /lib64/libxcb-cursor.so.0 \
    /lib/libxcb-cursor.so.0
  do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  done
}

append_exclude_modules() {
  local -n command_ref=$1
  shift
  local module_name
  for module_name in "${EXCLUDE_MODULES[@]}" "$@"; do
    command_ref+=(--exclude-module "$module_name")
  done
}

build_linux() {
  log "Linux onefile (GUI) 빌드 시작"

  local xcb_cursor_lib
  xcb_cursor_lib="$(find_xcb_cursor_library)"
  [[ -n "$xcb_cursor_lib" ]] || fail "libxcb-cursor.so.0를 찾을 수 없습니다. 예: sudo apt-get install libxcb-cursor0"

  local command=(
    "$PYTHON_CMD" -m PyInstaller
    --log-level WARN
    --noconfirm
    --clean
    --onefile
    --windowed
    --name "$APP_NAME"
    --paths "$ROOT_DIR"
    --hidden-import evdev
    --hidden-import pynput
    --hidden-import pynput.keyboard
    --hidden-import pynput.mouse
    --hidden-import serial.tools.list_ports
    --collect-submodules macrokey
    --distpath "$BUILD_ROOT/linux/dist"
    --workpath "$BUILD_ROOT/linux/build"
    --specpath "$BUILD_ROOT/linux/spec"
  )
  [[ -d "$PYINSTALLER_HOOKS_DIR" ]] && command+=(--additional-hooks-dir "$PYINSTALLER_HOOKS_DIR")
  [[ -f "$ICON_PNG" ]] && command+=(--icon "$ICON_PNG" --add-data "$ICON_PNG:assets")
  if compgen -G "$FIRMWARE_PREBUILT_DIR/firmware-*" >/dev/null; then
    command+=(--add-data "$FIRMWARE_PREBUILT_DIR:firmware/prebuilt")
    log "펌웨어 이미지 포함: $(ls "$FIRMWARE_PREBUILT_DIR" | tr '\n' ' ')"
  else
    log "경고: firmware/prebuilt/가 비어 있어 펌웨어 설치 기능 없이 빌드합니다 (./tools/build_firmware.sh)"
  fi
  append_exclude_modules command "${LINUX_EXCLUDE_MODULES[@]}"
  command+=(--add-binary "$xcb_cursor_lib:.")
  command+=("$ROOT_DIR/$ENTRY_SCRIPT")

  "${command[@]}"
  cp "$BUILD_ROOT/linux/dist/$APP_NAME" "$LINUX_RELEASE_DIR/$APP_NAME"
  chmod +x "$LINUX_RELEASE_DIR/$APP_NAME"
  log "Linux 결과물: $LINUX_RELEASE_DIR/$APP_NAME ($("$PYTHON_CMD" -c "import os; print(f'{os.path.getsize(\"$LINUX_RELEASE_DIR/$APP_NAME\")/1e6:.0f} MB')"))"
}

build_windows() {
  log "Windows onefile (GUI) 빌드 시작"

  local root_dir_win="$ROOT_DIR"
  local build_root_win="$BUILD_ROOT"
  local icon_ico_win="$ICON_ICO"
  local icon_png_win="$ICON_PNG"
  local hooks_dir_win="$PYINSTALLER_HOOKS_DIR"
  local entry_script_win="$ROOT_DIR/$ENTRY_SCRIPT"

  if command -v cygpath >/dev/null 2>&1; then
    root_dir_win="$(cygpath -w "$ROOT_DIR")"
    build_root_win="$(cygpath -w "$BUILD_ROOT")"
    icon_ico_win="$(cygpath -w "$ICON_ICO")"
    icon_png_win="$(cygpath -w "$ICON_PNG")"
    hooks_dir_win="$(cygpath -w "$PYINSTALLER_HOOKS_DIR")"
    entry_script_win="$(cygpath -w "$ROOT_DIR/$ENTRY_SCRIPT")"
  fi

  local command=(
    "$PYTHON_CMD" -m PyInstaller
    --log-level WARN
    --noconfirm
    --clean
    --onefile
    --windowed
    --name "$APP_NAME"
    --paths "$root_dir_win"
    --hidden-import pynput
    --hidden-import pynput.keyboard
    --hidden-import pynput.mouse
    --hidden-import serial.tools.list_ports
    --collect-submodules macrokey
    --distpath "$build_root_win/windows/dist"
    --workpath "$build_root_win/windows/build"
    --specpath "$build_root_win/windows/spec"
  )
  [[ -d "$PYINSTALLER_HOOKS_DIR" ]] && command+=(--additional-hooks-dir "$hooks_dir_win")
  [[ -f "$ICON_ICO" ]] && command+=(--icon "$icon_ico_win")
  [[ -f "$ICON_PNG" ]] && command+=(--add-data "$icon_png_win;assets")
  if compgen -G "$FIRMWARE_PREBUILT_DIR/firmware-*" >/dev/null; then
    local prebuilt_win="$FIRMWARE_PREBUILT_DIR"
    if command -v cygpath >/dev/null 2>&1; then
      prebuilt_win="$(cygpath -w "$FIRMWARE_PREBUILT_DIR")"
    fi
    command+=(--add-data "$prebuilt_win;firmware/prebuilt")
    log "펌웨어 이미지 포함: $(ls "$FIRMWARE_PREBUILT_DIR" | tr '\n' ' ')"
  else
    log "경고: firmware/prebuilt/가 비어 있어 펌웨어 설치 기능 없이 빌드합니다 (./tools/build_firmware.sh)"
  fi
  append_exclude_modules command "${WINDOWS_EXCLUDE_MODULES[@]}"
  command+=("$entry_script_win")

  "${command[@]}"
  cp "$BUILD_ROOT/windows/dist/$APP_NAME.exe" "$WINDOWS_RELEASE_DIR/$APP_NAME.exe"
  log "Windows 결과물: $WINDOWS_RELEASE_DIR/$APP_NAME.exe"
}

main() {
  parse_args "$@"
  cd "$ROOT_DIR"
  [[ -f "$ROOT_DIR/$ENTRY_SCRIPT" ]] || fail "엔트리 스크립트를 찾을 수 없습니다: $ENTRY_SCRIPT"

  detect_python
  ensure_deps
  if (( RUN_TESTS )); then
    run_tests
  fi
  prepare_dirs

  case "$CURRENT_OS" in
    Linux*)
      build_linux
      ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT*)
      build_windows
      ;;
    *)
      fail "지원하지 않는 OS입니다: $CURRENT_OS"
      ;;
  esac

  log "완료"
}

main "$@"
