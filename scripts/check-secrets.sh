#!/usr/bin/env bash
set -euo pipefail

ROOT="${CHECK_SECRETS_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT"

found=0
inside_git=0
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  inside_git=1
fi

report() {
  local file="$1"
  local line="$2"
  local reason="$3"
  printf '%s:%s: %s\n' "$file" "$line" "$reason"
  found=1
}

is_excluded_file() {
  local file="$1"
  if [[ "$inside_git" -eq 1 ]] && git check-ignore -q -- "$file"; then
    return 0
  fi
  case "$file" in
    ./.git/*|./.venv/*|./venv/*|./env/*|./.mypy_cache/*|./.ruff_cache/*|./.pytest_cache/*)
      return 0
      ;;
    ./__pycache__/*|*/__pycache__/*|*.pyc)
      return 0
      ;;
    ./.env.example|./config.example.yaml|./examples/config.yaml|./examples/systemd.env.example)
      return 0
      ;;
    ./meshcore-control-bridge/config.yaml|./home-assistant-addon/*/config.yaml)
      return 0
      ;;
    ./tests/fixtures/allowed-placeholders/*)
      return 0
      ;;
  esac
  return 1
}

while IFS= read -r -d '' file; do
  rel="./${file#./}"
  if is_excluded_file "$rel"; then
    continue
  fi
  case "$rel" in
    ./.env|./.env.*)
      report "$rel" 1 "real .env file must not be committed"
      ;;
    ./config.yaml|./config.local.yaml|*.local.yaml)
      report "$rel" 1 "real local config file must not be committed"
      ;;
    *.db|*.db-*|*.sqlite|*.sqlite3)
      report "$rel" 1 "SQLite database must not be committed"
      ;;
  esac
done < <(find . -type f -print0)

scan_pattern() {
  local pattern="$1"
  local reason="$2"
  while IFS=: read -r file line _; do
    [[ -z "${file:-}" || -z "${line:-}" ]] && continue
    if is_excluded_file "$file"; then
      continue
    fi
    report "$file" "$line" "$reason"
  done < <(grep -RInIE -e "$pattern" . \
    --exclude-dir=.git \
    --exclude-dir=.venv \
    --exclude-dir=venv \
    --exclude-dir=env \
    --exclude-dir=.mypy_cache \
    --exclude-dir=.ruff_cache \
    --exclude-dir=.pytest_cache \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' \
    --exclude='.env.example' \
    --exclude='config.example.yaml' \
    --exclude='check-secrets.sh' || true)
}

scan_pattern 'Bearer[[:space:]]+[A-Za-z0-9._~+/=-]{20,}' "possible Bearer token"
scan_pattern '-----BEGIN (RSA |EC |OPENSSH |DSA |PRIVATE )?PRIVATE KEY-----' "private key material"
scan_pattern '(^|[^A-Za-z])(password|passwd|pwd)[[:space:]]*[:=][[:space:]]*[^[:space:]#]{6,}' "possible password"
personal_domain='home[.]dedi[.]cat'
personal_node_one='pala''roaming'
personal_node_two='Pala''Repeater'
scan_pattern "${personal_domain}|${personal_node_one}|${personal_node_two}" \
  "forbidden personal reference"

while IFS=: read -r file line content; do
  [[ -z "${file:-}" || -z "${line:-}" ]] && continue
  if is_excluded_file "$file"; then
    continue
  fi
  if [[ "$content" == *"HA_TOKEN=replace-with-home-assistant-long-lived-access-token"* ]]; then
    continue
  fi
  if [[ "$content" =~ HA_TOKEN=[[:space:]]*($|#) ]]; then
    continue
  fi
  report "$file" "$line" "possible Home Assistant token assignment"
done < <(grep -RInI 'HA_TOKEN=' . \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude-dir=venv \
  --exclude-dir=env \
  --exclude-dir=.mypy_cache \
  --exclude-dir=.ruff_cache \
  --exclude-dir=.pytest_cache \
  --exclude-dir=__pycache__ \
  --exclude='*.pyc' \
  --exclude='.env.example' \
  --exclude='config.example.yaml' \
  --exclude='check-secrets.sh' || true)

if [[ "$found" -ne 0 ]]; then
  exit 1
fi
