#!/usr/bin/env bash
set -euo pipefail

root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
app_package="${root}/meshcore-control-bridge/package"

rm -rf "${app_package}"
mkdir -p "${app_package}"
cp -a "${root}/README.md" "${root}/LICENSE" "${root}/src" "${app_package}/"
cp -a "${root}/meshcore-control-bridge/package.pyproject.toml" "${app_package}/pyproject.toml"
rm -rf "${app_package}/src/meshcore_control/protocol"
rm -rf "${app_package}/src/meshcore_control/diagnostics"
rm -f "${app_package}/src/meshcore_control/transport/meshcore_usb.py"
