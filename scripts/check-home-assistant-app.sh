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
  meshcore-control-bridge/package/pyproject.toml
  meshcore-control-bridge/package/src/meshcore_control/main.py
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
assert config["version"] == "0.1.4"
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
assert config["options"]["allow_unidentified_readonly_testing"] is True
assert config["options"]["log_level"] == "debug"
assert config["schema"]["authorized_senders"][0]["role"] == "list(readonly|home|operator|admin)"
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

if find . -path ./.git -prune -o -path ./meshcore-control-bridge/config.yaml -prune -o -name config.yaml -print | grep -q .; then
  printf '%s\n' "unexpected recursive config.yaml outside the Home Assistant App directory" >&2
  find . -path ./.git -prune -o -path ./meshcore-control-bridge/config.yaml -prune -o -name config.yaml -print >&2
  exit 1
fi

grep -q 'SUPERVISOR_TOKEN' meshcore-control-bridge/run.sh
grep -q 'python3 -m meshcore_control.main --home-assistant-app' meshcore-control-bridge/run.sh
