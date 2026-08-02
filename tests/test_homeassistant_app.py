from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from meshcore_control import homeassistant_app_health
from meshcore_control.auth.roles import Role
from meshcore_control.homeassistant_app import (
    APP_DATABASE_PATH,
    SUPERVISOR_REST_BASE_URL,
    SUPERVISOR_WEBSOCKET_URL,
    HomeAssistantAppOptions,
    HomeAssistantRuntime,
    load_homeassistant_app_config,
    unidentified_testing_sender_id,
)


def test_homeassistant_app_options_load_valid_file(tmp_path) -> None:
    options_file = tmp_path / "options.json"
    options_file.write_text(
        json.dumps(
            {
                "channel_index": 1,
                "meshcore_entry_id": "entry-id",
                "command_prefix": "!",
                "authorized_senders": [
                    {
                        "pubkey_prefix": "abcdef123456",
                        "name": "admin-device",
                        "role": "admin",
                    }
                ],
                "status_entities": [
                    {
                        "alias": "temperature",
                        "entity_id": "sensor.living_room_temperature",
                        "label": "Temp",
                    }
                ],
                "rate_limit": {"commands": 5, "window_seconds": 60},
                "log_level": "info",
            }
        ),
        encoding="utf-8",
    )
    runtime = HomeAssistantRuntime(
        rest_base_url=SUPERVISOR_REST_BASE_URL,
        websocket_url=SUPERVISOR_WEBSOCKET_URL,
        token="supervisor-token-not-real",
    )

    config, options = load_homeassistant_app_config(str(options_file), runtime)

    assert options.channel_index == 1
    assert config.database_path == APP_DATABASE_PATH
    assert config.meshcore.transport == "homeassistant"
    assert config.meshcore.ha_entry_id == "entry-id"
    assert config.homeassistant.base_url == SUPERVISOR_REST_BASE_URL
    assert config.homeassistant.websocket_url == SUPERVISOR_WEBSOCKET_URL
    assert config.users["meshcore-pubkey-prefix:abcdef123456"].role is Role.admin
    assert config.status_entities["temperature"].entity_id == "sensor.living_room_temperature"


def test_homeassistant_app_rejects_public_channel_zero() -> None:
    with pytest.raises(ValueError, match="0/Public"):
        HomeAssistantAppOptions.from_mapping(
            {
                "channel_index": 0,
                "authorized_senders": [
                    {"pubkey_prefix": "abcdef123456", "name": "admin", "role": "readonly"}
                ],
            }
        )


def test_homeassistant_app_rejects_invalid_role() -> None:
    with pytest.raises(ValueError, match="invalid role"):
        HomeAssistantAppOptions.from_mapping(
            {
                "channel_index": 1,
                "authorized_senders": [
                    {"pubkey_prefix": "abcdef123456", "name": "admin", "role": "root"}
                ],
            }
        )


def test_homeassistant_app_requires_authorized_sender_by_default() -> None:
    with pytest.raises(ValueError, match="authorized sender"):
        HomeAssistantAppOptions.from_mapping({"channel_index": 1})


def test_homeassistant_app_supervisor_runtime_requires_token(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="SUPERVISOR_TOKEN"):
        HomeAssistantRuntime.from_supervisor_environment()


def test_homeassistant_app_supervisor_runtime_uses_internal_urls(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token-not-real")

    runtime = HomeAssistantRuntime.from_supervisor_environment()

    assert runtime.rest_base_url == "http://supervisor/core"
    assert runtime.websocket_url == "ws://supervisor/core/websocket"
    assert runtime.token == "supervisor-token-not-real"


def test_homeassistant_app_testing_mode_adds_readonly_synthetic_sender() -> None:
    runtime = HomeAssistantRuntime(
        rest_base_url=SUPERVISOR_REST_BASE_URL,
        websocket_url=SUPERVISOR_WEBSOCKET_URL,
        token="supervisor-token-not-real",
    )
    options = HomeAssistantAppOptions.from_mapping(
        {
            "channel_index": 1,
            "allow_unidentified_readonly_testing": True,
        }
    )

    config = options.to_app_config(runtime)

    user = config.users[unidentified_testing_sender_id(1)]
    assert user.role is Role.readonly
    assert config.meshcore.require_stable_sender is False


def test_homeassistant_app_healthcheck_accepts_recent_file(tmp_path, monkeypatch) -> None:
    health_file = tmp_path / "health.json"
    health_file.write_text(
        json.dumps({"status": "ok", "updated_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(homeassistant_app_health, "APP_HEALTHCHECK_PATH", str(health_file))

    homeassistant_app_health.main()
