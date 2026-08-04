#!/usr/bin/env bash
set -euo pipefail

ADDONS_ROOT="${MCB_TELEGRAM_TEST_ADDONS_ROOT:-${MCB_PR23_TEST_ADDONS_ROOT:-/addons}}"
TARGET_DIR="${ADDONS_ROOT%/}/meshcore-control-bridge-telegram-forwarding"
SOURCE_DIR="${ADDONS_ROOT%/}/.meshcore-control-bridge-telegram-forwarding-source"
REPO_URL="${MCB_TELEGRAM_TEST_REPO_URL:-${MCB_PR23_TEST_REPO_URL:-https://github.com/j3udiel/meshcore-control-bridge.git}}"
BRANCH="${MCB_TELEGRAM_TEST_BRANCH:-feat/telegram-to-meshcore-forwarding}"
EXPECTED_HEAD="${1:-}"
APP_CONFIG="${TARGET_DIR}/config.yaml"

if [[ "$(id -u)" -eq 0 ]]; then
  umask 022
fi

case "${TARGET_DIR}" in
  /addons/meshcore-control-bridge-telegram-forwarding) ;;
  "${MCB_TELEGRAM_TEST_ADDONS_ROOT:-${MCB_PR23_TEST_ADDONS_ROOT:-__unset__}}/meshcore-control-bridge-telegram-forwarding") ;;
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

if [[ -d "${TARGET_DIR}/.git" && ! -d "${SOURCE_DIR}/.git" ]]; then
  mv "${TARGET_DIR}" "${SOURCE_DIR}"
fi

if [[ -d "${SOURCE_DIR}/.git" ]]; then
  git -C "${SOURCE_DIR}" fetch origin "${BRANCH}"
  git -C "${SOURCE_DIR}" switch "${BRANCH}"
  git -C "${SOURCE_DIR}" reset --hard "origin/${BRANCH}"
elif [[ -e "${SOURCE_DIR}" ]]; then
  printf '%s\n' "${SOURCE_DIR} exists but is not a git checkout" >&2
  exit 1
else
  git clone --branch "${BRANCH}" "${REPO_URL}" "${SOURCE_DIR}"
fi

actual_head="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
if [[ "${actual_head}" != "${EXPECTED_HEAD}" ]]; then
  printf 'unexpected HEAD: %s\nexpected: %s\n' "${actual_head}" "${EXPECTED_HEAD}" >&2
  exit 1
fi

if [[ -e "${TARGET_DIR}" && ! -d "${TARGET_DIR}" ]]; then
  printf '%s\n' "${TARGET_DIR} exists but is not a directory" >&2
  exit 1
fi

mkdir -p "${TARGET_DIR}"

tmp_target="$(mktemp -d "${ADDONS_ROOT%/}/.meshcore-control-bridge-telegram-forwarding-build.XXXXXX")"
cp -R "${SOURCE_DIR}/meshcore-control-bridge/." "${tmp_target}/"
cp "${SOURCE_DIR}/pyproject.toml" "${tmp_target}/pyproject.toml"
cp "${SOURCE_DIR}/README.md" "${tmp_target}/README.md"
cp -R "${SOURCE_DIR}/src" "${tmp_target}/src"

tmp_dockerfile="$(mktemp "${tmp_target}/Dockerfile.tmp.XXXXXX")"
sed 's#COPY meshcore-control-bridge/run.sh /run.sh#COPY run.sh /run.sh#' \
  "${tmp_target}/Dockerfile" > "${tmp_dockerfile}"
mv "${tmp_dockerfile}" "${tmp_target}/Dockerfile"

tmp_config="$(mktemp "${tmp_target}/config.yaml.tmp.XXXXXX")"
awk '
  $0 == "name: MeshCore Control Bridge" {
    print "name: MeshCore Control Bridge Telegram Forwarding"
    next
  }
  $0 == "slug: meshcore_control_bridge" {
    print "slug: meshcore_control_bridge_telegram_forwarding"
    next
  }
  $0 == "image: \"ghcr.io/j3udiel/meshcore-control-bridge\"" {
    print "# image: \"ghcr.io/j3udiel/meshcore-control-bridge\""
    next
  }
  { print }
' "${tmp_target}/config.yaml" > "${tmp_config}"
chmod 0644 "${tmp_config}"
mv "${tmp_config}" "${tmp_target}/config.yaml"

for required in config.yaml Dockerfile run.sh pyproject.toml README.md src; do
  if [[ ! -e "${tmp_target}/${required}" ]]; then
    printf 'generated App is missing required path: %s\n' "${required}" >&2
    exit 1
  fi
done

if grep -q 'COPY meshcore-control-bridge/run.sh' "${tmp_target}/Dockerfile"; then
  printf '%s\n' "generated Dockerfile still references nested run.sh" >&2
  exit 1
fi

name_count="$(grep -cx 'name: MeshCore Control Bridge Telegram Forwarding' "${tmp_target}/config.yaml" || true)"
slug_count="$(grep -cx 'slug: meshcore_control_bridge_telegram_forwarding' "${tmp_target}/config.yaml" || true)"
commented_image_count="$(grep -cx '# image: "ghcr.io/j3udiel/meshcore-control-bridge"' "${tmp_target}/config.yaml" || true)"
if [[ "${name_count}" != "1" ]]; then
  printf 'invalid transformed App name count: %s\n' "${name_count}" >&2
  exit 1
fi
if [[ "${slug_count}" != "1" ]]; then
  printf 'invalid transformed App slug count: %s\n' "${slug_count}" >&2
  exit 1
fi
if grep -qx 'image: "ghcr.io/j3udiel/meshcore-control-bridge"' "${tmp_target}/config.yaml"; then
  printf '%s\n' "active GHCR image line remains in App config" >&2
  exit 1
fi
if [[ "${commented_image_count}" != "1" ]]; then
  printf 'invalid commented image line count: %s\n' "${commented_image_count}" >&2
  exit 1
fi

previous_dir="${ADDONS_ROOT%/}/.meshcore-control-bridge-telegram-forwarding-previous"
if [[ -e "${previous_dir}" ]]; then
  mv "${previous_dir}" "${previous_dir}.$$"
fi
if [[ -e "${TARGET_DIR}" ]]; then
  mv "${TARGET_DIR}" "${previous_dir}"
fi
mv "${tmp_target}" "${TARGET_DIR}"

printf '%s\n' "Prepared local Home Assistant App test copy:"
printf '  %s\n' "${TARGET_DIR}"
printf '%s\n' ""
printf '%s\n' "Next steps in Home Assistant:"
printf '%s\n' "1. Settings -> Apps -> App Store -> reload Local apps."
printf '%s\n' "2. Install 'MeshCore Control Bridge Telegram Forwarding'."
printf '%s\n' "3. Configure Telegram options in the test App."
printf '%s\n' "4. Start the test App and inspect logs."
printf '%s\n' ""
printf '%s\n' "No token, chat_id, or user_id was written by this script."
