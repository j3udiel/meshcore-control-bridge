#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="/addons/meshcore-control-bridge-pr23"
REPO_URL="https://github.com/j3udiel/meshcore-control-bridge.git"
BRANCH="feat/telegram-foundation"
EXPECTED_HEAD="${1:-}"
APP_CONFIG="${TARGET_DIR}/meshcore-control-bridge/config.yaml"

if [[ "$(id -u)" -eq 0 ]]; then
  umask 022
fi

case "${TARGET_DIR}" in
  /addons/meshcore-control-bridge-pr23) ;;
  *)
    printf '%s\n' "refusing unexpected target directory: ${TARGET_DIR}" >&2
    exit 1
    ;;
esac

mkdir -p /addons

if [[ -z "${EXPECTED_HEAD}" ]]; then
  EXPECTED_HEAD="$(git ls-remote "${REPO_URL}" "refs/heads/${BRANCH}" | awk '{print $1}')"
  if [[ -z "${EXPECTED_HEAD}" ]]; then
    printf 'could not resolve remote HEAD for %s\n' "${BRANCH}" >&2
    exit 1
  fi
fi

if [[ -d "${TARGET_DIR}/.git" ]]; then
  git -C "${TARGET_DIR}" fetch origin "${BRANCH}"
  git -C "${TARGET_DIR}" switch "${BRANCH}"
  git -C "${TARGET_DIR}" reset --hard "origin/${BRANCH}"
elif [[ -e "${TARGET_DIR}" ]]; then
  printf '%s\n' "${TARGET_DIR} exists but is not a git checkout" >&2
  exit 1
else
  git clone --branch "${BRANCH}" "${REPO_URL}" "${TARGET_DIR}"
fi

actual_head="$(git -C "${TARGET_DIR}" rev-parse HEAD)"
if [[ "${actual_head}" != "${EXPECTED_HEAD}" ]]; then
  printf 'unexpected HEAD: %s\nexpected: %s\n' "${actual_head}" "${EXPECTED_HEAD}" >&2
  exit 1
fi

if [[ ! -f "${APP_CONFIG}" ]]; then
  printf 'missing App config: %s\n' "${APP_CONFIG}" >&2
  exit 1
fi

python3 - "$APP_CONFIG" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
updated = []
for line in lines:
    if line.startswith("name: "):
        updated.append("name: MeshCore Control Bridge PR23")
    elif line.startswith("slug: "):
        updated.append("slug: meshcore_control_bridge_pr23")
    elif line.startswith("image: "):
        updated.append(f"# {line}")
    else:
        updated.append(line)
path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY

printf '%s\n' "Prepared local Home Assistant App test copy:"
printf '  %s\n' "${TARGET_DIR}"
printf '%s\n' ""
printf '%s\n' "Next steps in Home Assistant:"
printf '%s\n' "1. Settings -> Apps -> App Store -> reload Local apps."
printf '%s\n' "2. Install 'MeshCore Control Bridge PR23'."
printf '%s\n' "3. Configure Telegram options in the PR23 App."
printf '%s\n' "4. Start the PR23 App and inspect logs."
printf '%s\n' ""
printf '%s\n' "No token, chat_id, or user_id was written by this script."
