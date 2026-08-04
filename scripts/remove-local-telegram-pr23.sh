#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="/addons/meshcore-control-bridge-telegram-forwarding"

cat <<'TEXT'
This removes only the local Telegram forwarding test checkout:
  /addons/meshcore-control-bridge-telegram-forwarding

First stop and uninstall the local Telegram forwarding test App from Home Assistant.
This does not touch the stable App, other directories, Telegram bots, or tokens.
TEXT

printf 'Type "remove telegram forwarding" to continue: '
read -r confirmation

if [[ "${confirmation}" != "remove telegram forwarding" ]]; then
  printf '%s\n' "aborted"
  exit 1
fi

case "${TARGET_DIR}" in
  /addons/meshcore-control-bridge-telegram-forwarding)
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
