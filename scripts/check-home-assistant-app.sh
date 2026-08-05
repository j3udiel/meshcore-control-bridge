#!/usr/bin/env bash
set -euo pipefail

root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$root"

required_files=(
  repository.yaml
  meshcore-control-bridge/config.yaml
  meshcore-control-bridge/build.yaml
  meshcore-control-bridge/Dockerfile
  meshcore-control-bridge/run.sh
  meshcore-control-bridge/README.md
  meshcore-control-bridge/DOCS.md
  meshcore-control-bridge/CHANGELOG.md
  meshcore-control-bridge/translations/en.yaml
  meshcore-control-bridge/translations/es.yaml
)

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    printf '%s\n' "missing required Home Assistant App file: $file" >&2
    exit 1
  fi
done

if [[ ! -x meshcore-control-bridge/run.sh ]]; then
  printf '%s\n' "meshcore-control-bridge/run.sh must be executable" >&2
  exit 1
fi

python3 - <<'PY'
from pathlib import Path
import yaml

ALLOWED_CONFIG_KEYS = {
    "name",
    "slug",
    "description",
    "version",
    "startup",
    "boot",
    "init",
    "homeassistant_api",
    "stage",
    "image",
    "arch",
    "options",
    "schema",
}

repo = yaml.safe_load(Path("repository.yaml").read_text(encoding="utf-8"))
assert repo["name"] == "MeshCore Control Bridge Apps"

config = yaml.safe_load(Path("meshcore-control-bridge/config.yaml").read_text(encoding="utf-8"))
unknown = set(config) - ALLOWED_CONFIG_KEYS
assert not unknown, f"unknown or intentionally disallowed App config keys: {sorted(unknown)}"
assert config["slug"] == "meshcore_control_bridge"
assert config["version"] == "0.1.14"
assert config["homeassistant_api"] is True
assert config["stage"] == "experimental"
assert config["image"] == "ghcr.io/j3udiel/meshcore-control-bridge"
assert config["arch"] == ["amd64", "aarch64"]
assert "privileged" not in config
assert "host_network" not in config
assert "docker_api" not in config
assert "usb" not in config
assert "uart" not in config
assert "apparmor" not in config
assert "watchdog" not in config
assert config["schema"]["channel_index"] == "int(1,255)"
assert config["options"]["channel_index"] == 1
assert config["options"]["authorized_senders"] == []
assert config["options"]["weather_status"] == {
    "temperature_entity": "",
    "humidity_entity": "",
    "label": "Exterior",
}
assert config["options"]["telegram"] == {
    "enabled": False,
    "bot_token_import": "",
    "bot_token_file": "/data/telegram.bot_token",
    "allowed_private_chat_id": "",
    "allowed_user_id": "",
    "meshcore_channel_index": 1,
    "forward_meshcore_to_telegram": True,
    "forward_telegram_to_meshcore": True,
    "command_prefix": "!",
    "max_meshcore_message_length": 180,
    "max_telegram_message_length": 3900,
    "message_prefix": "TG: ",
    "meshcore_to_telegram_prefix": "MC: ",
    "forwarding_rate_limit": {
        "messages": 5,
        "window_seconds": 60,
    },
    "inbound_forwarding_rate_limit": {
        "messages": 20,
        "window_seconds": 60,
    },
}
assert config["options"]["allow_unidentified_readonly_testing"] is True
assert config["options"]["log_level"] == "debug"
assert config["schema"]["authorized_senders"][0]["role"] == "list(readonly|home|operator|admin)"
assert config["schema"]["weather_status"]["temperature_entity"] == "str?"
assert config["schema"]["weather_status"]["humidity_entity"] == "str?"
assert config["schema"]["telegram"]["bot_token_import"] == "password?"
assert config["schema"]["telegram"]["meshcore_channel_index"] == "int(1,255)"
assert config["schema"]["telegram"]["max_meshcore_message_length"] == "int(1,1000)"
assert config["schema"]["telegram"]["max_telegram_message_length"] == "int(1,4096)"
assert config["schema"]["telegram"]["forwarding_rate_limit"] == {
    "messages": "int(1,100)",
    "window_seconds": "int(1,3600)",
}
assert config["schema"]["telegram"]["inbound_forwarding_rate_limit"] == {
    "messages": "int(1,100)",
    "window_seconds": "int(1,3600)",
}
PY

if [[ -f meshcore-control-bridge/apparmor.txt ]]; then
  printf '%s\n' "custom AppArmor profile is intentionally omitted for this App version" >&2
  exit 1
fi

if grep -q '^ENTRYPOINT' meshcore-control-bridge/Dockerfile; then
  printf '%s\n' "Dockerfile must not override the Home Assistant base image ENTRYPOINT" >&2
  exit 1
fi

grep -q 'CMD \["/run.sh"\]' meshcore-control-bridge/Dockerfile
grep -q 'COPY pyproject.toml README.md /app/package/' meshcore-control-bridge/Dockerfile
grep -q 'COPY src /app/package/src' meshcore-control-bridge/Dockerfile

if [[ -e meshcore-control-bridge/package || -e meshcore-control-bridge/package.pyproject.toml ]]; then
  printf '%s\n' "duplicated vendored Python package is not allowed" >&2
  exit 1
fi

if [[ -e scripts/sync-home-assistant-app-package.sh ]]; then
  printf '%s\n' "manual package sync script is not allowed" >&2
  exit 1
fi

if find . -path ./.git -prune -o -path ./meshcore-control-bridge/config.yaml -prune -o -name config.yaml -print | grep -q .; then
  printf '%s\n' "unexpected recursive config.yaml outside the Home Assistant App directory" >&2
  find . -path ./.git -prune -o -path ./meshcore-control-bridge/config.yaml -prune -o -name config.yaml -print >&2
  exit 1
fi

grep -q 'SUPERVISOR_TOKEN' meshcore-control-bridge/run.sh
grep -q 'python3 -m meshcore_control.main --home-assistant-app' meshcore-control-bridge/run.sh
grep -q 'file: meshcore-control-bridge/Dockerfile' .github/workflows/publish-home-assistant-app.yml
if grep -q 'dockerfile: meshcore-control-bridge/Dockerfile' .github/workflows/publish-home-assistant-app.yml; then
  printf '%s\n' "publish workflow must use the Home Assistant builder 'file' input, not 'dockerfile'" >&2
  exit 1
fi
