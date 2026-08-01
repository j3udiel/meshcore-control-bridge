from __future__ import annotations

import stat
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_homeassistant_app_repository_layout() -> None:
    assert (ROOT / "repository.yaml").is_file()
    app_dir = ROOT / "meshcore-control-bridge"
    for name in [
        "config.yaml",
        "build.yaml",
        "Dockerfile",
        "run.sh",
        "README.md",
        "DOCS.md",
        "CHANGELOG.md",
        "apparmor.txt",
        "translations/en.yaml",
        "translations/es.yaml",
        "package/pyproject.toml",
        "package/src/meshcore_control/main.py",
    ]:
        assert (app_dir / name).is_file()


def test_homeassistant_app_config_is_restricted() -> None:
    config = yaml.safe_load((ROOT / "meshcore-control-bridge/config.yaml").read_text())

    assert config["homeassistant_api"] is True
    assert config["watchdog"] is True
    assert config["schema"]["channel_index"] == "int(1,255)"
    assert "privileged" not in config
    assert "host_network" not in config
    assert "docker_api" not in config
    assert "usb" not in config
    assert "uart" not in config


def test_homeassistant_app_run_script_is_executable() -> None:
    mode = (ROOT / "meshcore-control-bridge/run.sh").stat().st_mode

    assert mode & stat.S_IXUSR
