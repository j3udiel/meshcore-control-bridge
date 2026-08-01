#!/usr/bin/env bash
set -euo pipefail

root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
app_package="${root}/meshcore-control-bridge/package"

rm -rf "${app_package}"
mkdir -p "${app_package}"
cp -a "${root}/pyproject.toml" "${root}/README.md" "${root}/LICENSE" "${root}/src" "${app_package}/"
