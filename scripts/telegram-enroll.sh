#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${TELEGRAM_API_BASE_URL:-https://api.telegram.org}"
TIMEOUT_SECONDS=60

usage() {
  printf '%s\n' "usage: telegram-enroll.sh [--timeout seconds]"
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --timeout)
      if [[ "$#" -lt 2 ]]; then
        usage >&2
        exit 2
      fi
      TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

case "${TIMEOUT_SECONDS}" in
  ''|*[!0-9]*)
    printf '%s\n' "timeout must be a positive integer" >&2
    exit 2
    ;;
esac
if [[ "${TIMEOUT_SECONDS}" -lt 1 ]]; then
  printf '%s\n' "timeout must be a positive integer" >&2
  exit 2
fi

cleanup() {
  TOKEN=""
  RESPONSE=""
  UPDATES=""
}
trap cleanup EXIT INT TERM

printf 'Telegram bot token: ' >&2
IFS= read -rs TOKEN
printf '\n' >&2

if [[ -z "${TOKEN}" ]]; then
  printf '%s\n' "token is required" >&2
  exit 2
fi

telegram_post() {
  method="$1"
  payload="$2"
  if ! raw_response="$(curl -sS \
    -w '
%{http_code}' \
    -H 'Content-Type: application/json' \
    -X POST \
    --data "${payload}" \
    "${API_BASE_URL%/}/bot${TOKEN}/${method}")"; then
    printf 'Telegram %s request failed\n' "${method}" >&2
    return 1
  fi
  status="${raw_response##*$'\n'}"
  RESPONSE="${raw_response%$'\n'*}"
  if [[ "${status}" == "409" ]]; then
    printf '%s\n' "Telegram returned HTTP 409. Another consumer or webhook is active for this bot." >&2
    return 1
  fi
  if [[ "${status}" -lt 200 || "${status}" -ge 300 ]]; then
    printf 'Telegram %s returned HTTP %s\n' "${method}" "${status}" >&2
    return 1
  fi
  return 0
}

if ! telegram_post "getMe" '{}'; then
  exit 1
fi
if ! printf '%s' "${RESPONSE}" | grep -q '"ok":true'; then
  printf '%s\n' "Telegram getMe failed" >&2
  exit 1
fi
printf '%s\n' "Telegram bot token accepted." >&2
printf '%s\n' "Open the private chat with the bot, press Start, and send one text message." >&2

offset=""
if telegram_post "getUpdates" '{"timeout":0,"allowed_updates":["message"]}'; then
  max_update_id="$(printf '%s' "${RESPONSE}" \
    | grep -o '"update_id":[0-9][0-9]*' \
    | sed 's/[^0-9]//g' \
    | awk 'BEGIN { max = -1 } { if ($1 > max) max = $1 } END { if (max >= 0) print max }' \
    || true)"
  if [[ -n "${max_update_id}" ]]; then
    offset=$((max_update_id + 1))
  fi
fi

SECONDS=0
while [[ "${SECONDS}" -lt "${TIMEOUT_SECONDS}" ]]; do
  remaining=$(( TIMEOUT_SECONDS - SECONDS ))
  if [[ "${remaining}" -lt 1 ]]; then
    break
  fi
  poll_timeout="${remaining}"
  if [[ "${poll_timeout}" -gt 10 ]]; then
    poll_timeout=10
  fi
  payload="{\"timeout\":${poll_timeout},\"allowed_updates\":[\"message\"]"
  if [[ -n "${offset}" ]]; then
    payload="${payload},\"offset\":${offset}"
  fi
  payload="${payload}}"
  if ! telegram_post "getUpdates" "${payload}"; then
    exit 1
  fi
  update_ids="$(printf '%s' "${RESPONSE}" | grep -o '"update_id":[0-9][0-9]*' | sed 's/[^0-9]//g' || true)"
  if [[ -n "${update_ids}" ]]; then
    max_seen="$(printf '%s\n' "${update_ids}" | awk 'BEGIN { max = -1 } { if ($1 > max) max = $1 } END { print max }')"
    if [[ "${max_seen}" -ge 0 ]]; then
      offset=$((max_seen + 1))
    fi
  fi

  candidate_lines="$(printf '%s' "${RESPONSE}" | sed 's/},{"update_id"/}\n{"update_id"/g')"
  while IFS= read -r line; do
    if ! printf '%s' "${line}" | grep -q '"message":'; then
      continue
    fi
    if ! printf '%s' "${line}" | grep -q '"text":'; then
      continue
    fi
    if ! printf '%s' "${line}" | grep -q '"chat":{[^}]*"type":"private"'; then
      continue
    fi
    if ! printf '%s' "${line}" | grep -q '"from":{[^}]*"is_bot":false'; then
      continue
    fi
    chat_id="$(printf '%s' "${line}" \
      | sed -n 's/.*"chat":{"id":\(-\{0,1\}[0-9][0-9]*\)[^}]*"type":"private".*/\1/p')"
    user_id="$(printf '%s' "${line}" \
      | sed -n 's/.*"from":{"id":\([0-9][0-9]*\)[^}]*"is_bot":false.*/\1/p')"
    if [[ -n "${chat_id}" && -n "${user_id}" ]]; then
      printf 'allowed_private_chat_id: "%s"\n' "${chat_id}"
      printf 'allowed_user_id: "%s"\n' "${user_id}"
      exit 0
    fi
  done <<EOF
${candidate_lines}
EOF
done

printf '%s\n' "timed out waiting for a private human text message" >&2
exit 1
