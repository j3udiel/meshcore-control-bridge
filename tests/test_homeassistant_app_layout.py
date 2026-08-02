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
        "translations/en.yaml",
        "translations/es.yaml",
        "package/pyproject.toml",
        "package/src/meshcore_control/main.py",
    ]:
        assert (app_dir / name).is_file()


def test_homeassistant_app_config_is_restricted() -> None:
    config = yaml.safe_load((ROOT / "meshcore-control-bridge/config.yaml").read_text())

    allowed_config_keys = {
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

    assert set(config) <= allowed_config_keys
    assert config["version"] == "0.1.2"
    assert config["homeassistant_api"] is True
    assert config["stage"] == "experimental"
    assert config["image"] == "ghcr.io/j3udiel/meshcore-control-bridge"
    assert config["arch"] == ["amd64", "aarch64"]
    assert config["schema"]["channel_index"] == "int(1,255)"
    assert config["schema"]["authorized_senders"][0]["role"] == (
        "list(readonly|home|operator|admin)"
    )
    assert config["options"]["allow_unidentified_readonly_testing"] is True
    assert config["options"]["log_level"] == "debug"
    assert "privileged" not in config
    assert "host_network" not in config
    assert "docker_api" not in config
    assert "usb" not in config
    assert "uart" not in config
    assert "apparmor" not in config
    assert "watchdog" not in config
    assert not (ROOT / "meshcore-control-bridge/apparmor.txt").exists()


def test_only_app_directory_contains_supervisor_config_yaml() -> None:
    config_paths = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("config.yaml")
        if ".git" not in path.parts
    }

    assert config_paths == {"meshcore-control-bridge/config.yaml"}


def test_homeassistant_app_run_script_is_executable() -> None:
    mode = (ROOT / "meshcore-control-bridge/run.sh").stat().st_mode

    assert mode & stat.S_IXUSR


def test_homeassistant_app_preserves_base_entrypoint() -> None:
    dockerfile = (ROOT / "meshcore-control-bridge/Dockerfile").read_text()

    assert "ENTRYPOINT" not in dockerfile
    assert 'CMD ["/run.sh"]' in dockerfile
