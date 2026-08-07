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
    ]:
        assert (app_dir / name).is_file()
    assert not (app_dir / "package").exists()
    assert not (app_dir / "package.pyproject.toml").exists()
    assert not (ROOT / "scripts/sync-home-assistant-app-package.sh").exists()


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
    assert config["version"] == "0.1.21"
    assert config["homeassistant_api"] is True
    assert config["stage"] == "experimental"
    assert config["image"] == "ghcr.io/j3udiel/meshcore-control-bridge"
    assert config["arch"] == ["amd64", "aarch64"]
    assert config["schema"]["channel_index"] == "int(1,255)"
    assert config["schema"]["authorized_senders"][0]["role"] == (
        "list(readonly|home|operator|admin)"
    )
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
        "authorized_user_role": "readonly",
        "meshcore_channel_index": 1,
        "forward_meshcore_to_telegram": True,
        "forward_telegram_to_meshcore": True,
        "command_prefix": "!",
        "max_meshcore_message_length": 180,
        "max_telegram_message_length": 3900,
        "message_prefix": "TG: ",
        "meshcore_to_telegram_prefix": "MC: ",
        "send_forward_confirmation": False,
        "forwarding_rate_limit": {"messages": 5, "window_seconds": 60},
        "inbound_forwarding_rate_limit": {"messages": 20, "window_seconds": 60},
    }
    assert config["schema"]["weather_status"] == {
        "temperature_entity": "str?",
        "humidity_entity": "str?",
        "label": "str",
    }
    assert config["schema"]["telegram"]["bot_token_import"] == "password?"
    assert config["schema"]["telegram"]["authorized_user_role"] == (
        "list(readonly|home|operator|admin)"
    )
    assert config["schema"]["telegram"]["meshcore_channel_index"] == "int(1,255)"
    assert config["schema"]["telegram"]["max_meshcore_message_length"] == "int(1,1000)"
    assert config["schema"]["telegram"]["max_telegram_message_length"] == "int(1,4096)"
    assert config["schema"]["telegram"]["send_forward_confirmation"] == "bool"
    assert config["schema"]["telegram"]["forwarding_rate_limit"] == {
        "messages": "int(1,100)",
        "window_seconds": "int(1,3600)",
    }
    assert config["schema"]["telegram"]["inbound_forwarding_rate_limit"] == {
        "messages": "int(1,100)",
        "window_seconds": "int(1,3600)",
    }
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


def test_homeassistant_health_integration_docs_cover_template_entities() -> None:
    docs = (ROOT / "meshcore-control-bridge/DOCS.md").read_text()

    assert "Home Assistant Health Integration" in docs
    assert "schema_version" in docs
    assert "supported public API for registering native" in docs
    assert "entities" in docs
    for unique_id in [
        "meshcore_control_bridge_status",
        "meshcore_control_bridge_version",
        "meshcore_control_bridge_uptime",
        "meshcore_control_bridge_meshcore",
        "meshcore_control_bridge_telegram",
        "meshcore_control_bridge_last_tg_to_mc",
        "meshcore_control_bridge_last_mc_to_tg",
        "meshcore_control_bridge_last_error",
        "meshcore_control_bridge_tg_to_mc_success",
        "meshcore_control_bridge_tg_to_mc_failed",
        "meshcore_control_bridge_mc_to_tg_success",
        "meshcore_control_bridge_mc_to_tg_failed",
        "meshcore_control_bridge_commands_processed",
        "meshcore_control_bridge_audit_db",
        "meshcore_control_bridge_telegram_db",
    ]:
        assert unique_id in docs
