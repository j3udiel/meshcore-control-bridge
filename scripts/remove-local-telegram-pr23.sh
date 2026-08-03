#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="/addons/meshcore-control-bridge-pr23"

cat <<'TEXT'
This removes only the local PR23 checkout:
  /addons/meshcore-control-bridge-pr23

First stop and uninstall the local PR23 App from Home Assistant.
This does not touch the stable App, other directories, Telegram bots, or tokens.
TEXT

printf 'Type "remove PR23" to continue: '
read -r confirmation

if [[ "${confirmation}" != "remove PR23" ]]; then
  printf '%s\n' "aborted"
  exit 1
fi

case "${TARGET_DIR}" in
  /addons/meshcore-control-bridge-pr23)
    if [[ -e "${TARGET_DIR}" ]]; then
      rm -rf -- "${TARGET_DIR}"
      printf 'removed %s\n' "${TARGET_DIR}"
    else
      printf 'nothing to remove: %s\n' "${TARGET_DIR}"
    fi
    ;;
  *)
    printf '%s\n' "refusing unexpected target directory: ${TARGET_DIR}" >&2
    exit 1
    ;;
esac
