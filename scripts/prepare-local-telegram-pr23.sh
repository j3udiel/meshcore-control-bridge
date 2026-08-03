#!/usr/bin/env bash
set -euo pipefail

ADDONS_ROOT="${MCB_PR23_TEST_ADDONS_ROOT:-/addons}"
TARGET_DIR="${ADDONS_ROOT%/}/meshcore-control-bridge-pr23"
REPO_URL="${MCB_PR23_TEST_REPO_URL:-https://github.com/j3udiel/meshcore-control-bridge.git}"
BRANCH="feat/telegram-foundation"
EXPECTED_HEAD="${1:-}"
APP_CONFIG="${TARGET_DIR}/meshcore-control-bridge/config.yaml"

if [[ "$(id -u)" -eq 0 ]]; then
  umask 022
fi

case "${TARGET_DIR}" in
  /addons/meshcore-control-bridge-pr23) ;;
  "${MCB_PR23_TEST_ADDONS_ROOT:-__unset__}/meshcore-control-bridge-pr23") ;;
  *)
    printf '%s\n' "refusing unexpected target directory: ${TARGET_DIR}" >&2
    exit 1
    ;;
esac

mkdir -p "${ADDONS_ROOT}"

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

tmp_config="$(mktemp "${APP_CONFIG}.tmp.XXXXXX")"
awk '
  $0 == "name: MeshCore Control Bridge" {
    print "name: MeshCore Control Bridge PR23"
    next
  }
  $0 == "slug: meshcore_control_bridge" {
    print "slug: meshcore_control_bridge_pr23"
    next
  }
  $0 == "image: \"ghcr.io/j3udiel/meshcore-control-bridge\"" {
    print "# image: \"ghcr.io/j3udiel/meshcore-control-bridge\""
    next
  }
  { print }
' "${APP_CONFIG}" > "${tmp_config}"
chmod 0644 "${tmp_config}"
mv "${tmp_config}" "${APP_CONFIG}"

name_count="$(grep -cx 'name: MeshCore Control Bridge PR23' "${APP_CONFIG}" || true)"
slug_count="$(grep -cx 'slug: meshcore_control_bridge_pr23' "${APP_CONFIG}" || true)"
commented_image_count="$(grep -cx '# image: "ghcr.io/j3udiel/meshcore-control-bridge"' "${APP_CONFIG}" || true)"
if [[ "${name_count}" != "1" ]]; then
  printf 'invalid transformed App name count: %s\n' "${name_count}" >&2
  exit 1
fi
if [[ "${slug_count}" != "1" ]]; then
  printf 'invalid transformed App slug count: %s\n' "${slug_count}" >&2
  exit 1
fi
if grep -qx 'image: "ghcr.io/j3udiel/meshcore-control-bridge"' "${APP_CONFIG}"; then
  printf '%s\n' "active GHCR image line remains in App config" >&2
  exit 1
fi
if [[ "${commented_image_count}" != "1" ]]; then
  printf 'invalid commented image line count: %s\n' "${commented_image_count}" >&2
  exit 1
fi

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
