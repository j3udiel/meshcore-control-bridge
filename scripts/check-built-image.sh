#!/usr/bin/env bash
set -euo pipefail

image="${1:-meshcore-control-bridge-ha-app:test}"

entrypoint="$(docker inspect "${image}" --format '{{json .Config.Entrypoint}}')"
cmd="$(docker inspect "${image}" --format '{{json .Config.Cmd}}')"

if [[ "${entrypoint}" != '["/init"]' ]]; then
  printf 'unexpected image entrypoint: %s\n' "${entrypoint}" >&2
  exit 1
fi

if [[ "${cmd}" != '["/run.sh"]' ]]; then
  printf 'unexpected image command: %s\n' "${cmd}" >&2
  exit 1
fi

docker run --rm --entrypoint /bin/sh "${image}" -lc 'python3 - <<'"'"'PY'"'"'
import asyncio
import inspect
import sqlite3

import meshcore_control
from meshcore_control.adapters.homeassistant import HomeAssistantStatus
from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer
from meshcore_control.auth.roles import Role
from meshcore_control.commands.router import CommandRouter
from meshcore_control.homeassistant_app import unidentified_testing_sender_id
from meshcore_control.models import InboundMessage, OutboundMessage
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.repositories import AuditRepository
import meshcore_control.adapters.homeassistant_ws as ws
import meshcore_control.commands.router as router
import meshcore_control.homeassistant_app_health as health

assert meshcore_control.__version__ == "0.1.16"
assert unidentified_testing_sender_id(1) == "test:unidentified:channel:1"
assert "authorization=" in inspect.getsource(router)
assert "on_idle" in inspect.getsource(ws)
assert "healthcheck is stale" in inspect.getsource(health)


class Transport:
    def __init__(self) -> None:
        self.sent = []

    async def receive(self):
        raise NotImplementedError

    async def send(self, message: OutboundMessage) -> None:
        self.sent.append(message)


class HA:
    async def check_available(self) -> HomeAssistantStatus:
        return HomeAssistantStatus(available=True, message="OK")

    async def get_config(self):
        return {"version": "2026.8.0", "location_name": "Home"}


async def main() -> None:
    sender = unidentified_testing_sender_id(1)
    connection = connect_database(":memory:")
    registry = build_registry()
    transport = Transport()
    router_instance = CommandRouter(
        registry=registry,
        authorizer=Authorizer({sender: AuthorizedUser(sender, "test", Role.readonly)}),
        audit=AuditRepository(connection),
        services={"registry": registry, "homeassistant": HA()},
        prefix="!",
    )
    service = BridgeService(
        transport=transport,
        router=router_instance,
        deduplicator=Deduplicator(connection, window_seconds=300),
        rate_limiter=RateLimiter(max_commands=5, window_seconds=60),
        channel_index=1,
    )
    outbound = await service.process_message(
        InboundMessage(
            transport="homeassistant-meshcore",
            message_id="artifact-1",
            sender_id=sender,
            channel_index=1,
            text="!ping",
        )
    )
    assert outbound is not None
    assert outbound.text == "pong"
    assert transport.sent[-1].channel_index == 1


asyncio.run(main())
print("built-image-ok")
PY'

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmpdir}"
}
trap cleanup EXIT

cat > "${tmpdir}/options.json" <<'JSON'
{
  "channel_index": 1,
  "meshcore_entry_id": "",
  "command_prefix": "!",
  "authorized_senders": [],
  "status_entities": [],
  "rate_limit": {
    "commands": 5,
    "window_seconds": 60
  },
  "log_level": "info",
  "allow_unidentified_readonly_testing": true
}
JSON

set +e
run_startup() {
  timeout 12s docker run --rm \
    -e SUPERVISOR_TOKEN=dummy-supervisor-token \
    -v "${tmpdir}:/data" \
    "${image}" 2>&1
}

startup_output="$(run_startup)"
startup_status=$?
set -e

if [[ "${startup_status}" != 0 && "${startup_status}" != 1 && "${startup_status}" != 124 ]]; then
  printf 'unexpected startup status: %s\n' "${startup_status}" >&2
  printf '%s\n' "${startup_output}" | sed 's/dummy-supervisor-token/[REDACTED]/g' >&2
  exit 1
fi

check_startup_output() {
  local output="$1"

  if [[ "${output}" != *"Home Assistant App runtime detected"* ]]; then
    printf '%s\n' "image did not reach Home Assistant App runtime startup" >&2
    printf '%s\n' "${output}" | sed 's/dummy-supervisor-token/[REDACTED]/g' >&2
    exit 1
  fi

  if [[ "${output}" == *"/config/config.yaml"* ]] || [[ "${output}" == *"config file does not exist"* ]]; then
    printf '%s\n' "image attempted to use standalone YAML configuration" >&2
    printf '%s\n' "${output}" | sed 's/dummy-supervisor-token/[REDACTED]/g' >&2
    exit 1
  fi

  for forbidden in \
    "dummy-supervisor-token" \
    "SUPERVISOR_TOKEN" \
    "audit_key" \
    "meshcore-pubkey-prefix:" \
    "artifact-1" \
    "!ping" \
    "private-message"; do
    if [[ "${output}" == *"${forbidden}"* ]]; then
      printf 'image startup logs exposed forbidden marker: %s\n' "${forbidden}" >&2
      exit 1
    fi
  done
}

check_startup_output "${startup_output}"

if [[ ! -f "${tmpdir}/audit.key" ]]; then
  printf '%s\n' "Home Assistant App startup did not create /data/audit.key" >&2
  exit 1
fi

key_mode="$(
  docker run --rm --entrypoint /bin/sh \
    -v "${tmpdir}:/data" \
    "${image}" \
    -lc "stat -c '%a' /data/audit.key"
)"
if [[ "${key_mode}" != "600" ]]; then
  printf 'unexpected /data/audit.key mode: %s\n' "${key_mode}" >&2
  exit 1
fi

key_hash_before="$(
  docker run --rm --entrypoint /bin/sh \
    -v "${tmpdir}:/data" \
    "${image}" \
    -lc "sha256sum /data/audit.key | awk '{print \$1}'"
)"

docker run --rm --entrypoint /bin/sh \
  -v "${tmpdir}:/data" \
  "${image}" \
  -lc 'python3 - <<'"'"'PY'"'"'
import sqlite3

connection = sqlite3.connect("/data/audit.db")
tables = {
    row[0] for row in connection.execute("SELECT name FROM sqlite_master")
}
required = {"audit_metadata", "normalized_audit_events"}
missing = required - tables
if missing:
    raise SystemExit(f"missing normalized audit tables: {sorted(missing)}")
PY
'

set +e
second_startup_output="$(run_startup)"
second_startup_status=$?
set -e

if [[ "${second_startup_status}" != 0 && "${second_startup_status}" != 1 && "${second_startup_status}" != 124 ]]; then
  printf 'unexpected second startup status: %s\n' "${second_startup_status}" >&2
  printf '%s\n' "${second_startup_output}" | sed 's/dummy-supervisor-token/[REDACTED]/g' >&2
  exit 1
fi

check_startup_output "${second_startup_output}"

key_hash_after="$(
  docker run --rm --entrypoint /bin/sh \
    -v "${tmpdir}:/data" \
    "${image}" \
    -lc "sha256sum /data/audit.key | awk '{print \$1}'"
)"
if [[ "${key_hash_before}" != "${key_hash_after}" ]]; then
  printf '%s\n' "/data/audit.key was not reused across restarts" >&2
  exit 1
fi
