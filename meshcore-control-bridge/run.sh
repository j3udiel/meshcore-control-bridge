#!/usr/bin/with-contenv bashio
set -euo pipefail

if [[ -z "${SUPERVISOR_TOKEN:-}" ]]; then
  bashio::log.fatal "SUPERVISOR_TOKEN is unavailable"
fi

if [[ ! -f /data/options.json ]]; then
  bashio::log.fatal "/data/options.json is unavailable"
fi

exec python3 -m meshcore_control.main --home-assistant-app

