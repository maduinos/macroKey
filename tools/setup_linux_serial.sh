#!/usr/bin/env bash
set -euo pipefail

# Give the desktop user access to Arduino/RP2040 CDC serial devices. Running
# the script through sudo still targets the caller via SUDO_USER; an explicit
# username can be supplied when the login account cannot be inferred.
target_user="${1:-${SUDO_USER:-${USER:-}}}"

if [[ -z "$target_user" || "$target_user" == "root" ]]; then
  echo "사용자 계정을 확인할 수 없습니다: $0 <ubuntu-user>" >&2
  exit 2
fi

if ! id "$target_user" >/dev/null 2>&1; then
  echo "존재하지 않는 사용자입니다: $target_user" >&2
  exit 2
fi

if ! getent group dialout >/dev/null 2>&1; then
  echo "dialout 그룹이 없습니다. Ubuntu/Debian 계열 환경인지 확인하세요." >&2
  exit 2
fi

if [[ " $(id -nG "$target_user") " == *" dialout "* ]]; then
  echo "$target_user 계정은 이미 dialout 그룹에 있습니다."
  exit 0
fi

if (( EUID == 0 )); then
  usermod -aG dialout "$target_user"
else
  sudo usermod -aG dialout "$target_user"
fi

echo "$target_user 계정을 dialout 그룹에 추가했습니다."
echo "USB 시리얼 권한을 적용하려면 로그아웃한 뒤 다시 로그인하세요."
