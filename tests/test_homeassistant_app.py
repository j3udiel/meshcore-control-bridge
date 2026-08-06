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
    configured_temperature = ".".join(("sensor", "configured_temperature"))
    configured_humidity = ".".join(("sensor", "configured_humidity"))
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
                "weather_status": {
                    "temperature_entity": configured_temperature,
                    "humidity_entity": configured_humidity,
                    "label": "Patio",
                },
                "telegram": {
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
                },
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
    assert config.room_policies["homeassistant-meshcore:channel:1"].minimum_role is Role.readonly
    assert config.status_entities["temperature"].entity_id == "sensor.living_room_temperature"
    assert config.weather_status.temperature_entity == configured_temperature
    assert config.weather_status.humidity_entity == configured_humidity
    assert config.weather_status.label == "Patio"
    assert config.telegram.enabled is False
    assert config.telegram.bot_token_file == "/data/telegram.bot_token"
    assert config.telegram.meshcore_channel_index == 1
    assert config.telegram.authorized_user_role is Role.readonly
    assert config.telegram.message_prefix == "TG: "
    assert config.telegram.meshcore_to_telegram_prefix == "MC: "
    assert config.telegram.send_forward_confirmation is False
    assert config.telegram.max_telegram_message_length == 3900
    assert config.telegram.forwarding_rate_limit.commands == 5
    assert config.telegram.inbound_forwarding_rate_limit.commands == 20
    assert options.health.home_assistant_events_enabled is True
    assert options.health.heartbeat_seconds == 60
    assert config.health.home_assistant_events_enabled is True
    assert config.health.heartbeat_seconds == 60


def test_homeassistant_app_telegram_role_can_be_admin_without_touching_senders() -> None:
    options = HomeAssistantAppOptions.from_mapping(
        {
            "authorized_senders": [
                {"pubkey_prefix": "abcdef123456", "name": "mesh", "role": "operator"}
            ],
            "telegram": {"authorized_user_role": "admin"},
        }
    )
    runtime = HomeAssistantRuntime(
        rest_base_url=SUPERVISOR_REST_BASE_URL,
        websocket_url=SUPERVISOR_WEBSOCKET_URL,
        token="supervisor-token-not-real",
    )

    config = options.to_app_config(runtime)

    assert options.authorized_senders[0].role is Role.operator
    assert config.telegram.authorized_user_role is Role.admin


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


def test_homeassistant_app_telegram_enabled_requires_private_chat_and_user() -> None:
    with pytest.raises(ValueError, match="allowed_private_chat_id"):
        HomeAssistantAppOptions.from_mapping(
            {
                "channel_index": 1,
                "allow_unidentified_readonly_testing": True,
                "telegram": {"enabled": True},
            }
        )


def test_homeassistant_app_telegram_defaults_match_yaml_defaults() -> None:
    options = HomeAssistantAppOptions.from_mapping(
        {
            "channel_index": 1,
            "allow_unidentified_readonly_testing": True,
            "telegram": {},
        }
    )

    assert options.telegram.message_prefix == "TG: "
    assert options.telegram.meshcore_to_telegram_prefix == "MC: "
    assert options.telegram.send_forward_confirmation is False
    assert options.telegram.forwarding_rate_limit.commands == 5
    assert options.telegram.forwarding_rate_limit.window_seconds == 60
    assert options.telegram.inbound_forwarding_rate_limit.commands == 20
    assert options.telegram.inbound_forwarding_rate_limit.window_seconds == 60
    assert options.health.home_assistant_events_enabled is True
    assert options.health.heartbeat_seconds == 60


def test_homeassistant_app_health_options_load_defaults_for_old_options() -> None:
    options = HomeAssistantAppOptions.from_mapping(
        {
            "channel_index": 1,
            "allow_unidentified_readonly_testing": True,
        }
    )

    assert options.health.home_assistant_events_enabled is True
    assert options.health.heartbeat_seconds == 60


@pytest.mark.parametrize("value", [None, "true", "false", 1])
def test_homeassistant_app_health_events_enabled_requires_boolean(value: object) -> None:
    with pytest.raises(ValueError, match="health.home_assistant_events_enabled"):
        HomeAssistantAppOptions.from_mapping(
            {
                "channel_index": 1,
                "allow_unidentified_readonly_testing": True,
                "health": {"home_assistant_events_enabled": value},
            }
        )


@pytest.mark.parametrize("value", [14, 3601])
def test_homeassistant_app_health_heartbeat_range(value: int) -> None:
    with pytest.raises(ValueError, match="health.heartbeat_seconds"):
        HomeAssistantAppOptions.from_mapping(
            {
                "channel_index": 1,
                "allow_unidentified_readonly_testing": True,
                "health": {"heartbeat_seconds": value},
            }
        )


def test_homeassistant_app_telegram_rejects_invalid_forwarding_prefix() -> None:
    with pytest.raises(ValueError, match="control characters"):
        HomeAssistantAppOptions.from_mapping(
            {
                "channel_index": 1,
                "allow_unidentified_readonly_testing": True,
                "telegram": {"message_prefix": "TG:\t"},
            }
        )


def test_homeassistant_app_telegram_rejects_invalid_inbound_forwarding_prefix() -> None:
    with pytest.raises(ValueError, match="meshcore_to_telegram_prefix"):
        HomeAssistantAppOptions.from_mapping(
            {
                "channel_index": 1,
                "allow_unidentified_readonly_testing": True,
                "telegram": {"meshcore_to_telegram_prefix": "MC:\n"},
            }
        )


@pytest.mark.parametrize("value", [None, "false", "true"])
def test_homeassistant_app_telegram_rejects_non_boolean_forward_confirmation(
    value: object,
) -> None:
    with pytest.raises(ValueError, match="send_forward_confirmation"):
        HomeAssistantAppOptions.from_mapping(
            {
                "channel_index": 1,
                "allow_unidentified_readonly_testing": True,
                "telegram": {"send_forward_confirmation": value},
            }
        )


def test_homeassistant_app_requires_authorized_sender_by_default() -> None:
    with pytest.raises(ValueError, match="authorized sender"):
        HomeAssistantAppOptions.from_mapping({"channel_index": 1, "authorized_senders": []})


def test_homeassistant_app_reports_missing_authorized_senders_as_migration_issue() -> None:
    with pytest.raises(ValueError, match="authorized_senders is missing"):
        HomeAssistantAppOptions.from_mapping({"channel_index": 1})


def test_homeassistant_app_rejects_null_authorized_senders() -> None:
    with pytest.raises(ValueError, match="authorized_senders must be a list"):
        HomeAssistantAppOptions.from_mapping({"channel_index": 1, "authorized_senders": None})


def test_homeassistant_app_loads_valid_authorized_sender() -> None:
    options = HomeAssistantAppOptions.from_mapping(
        {
            "channel_index": 1,
            "authorized_senders": [
                {"pubkey_prefix": "abcdef123456", "name": "admin", "role": "readonly"}
            ],
        }
    )

    assert options.authorized_senders[0].pubkey_prefix == "abcdef123456"
    assert options.authorized_senders[0].role is Role.readonly


def test_homeassistant_app_options_json_round_trip_preserves_authorized_senders(tmp_path) -> None:
    options_file = tmp_path / "options.json"
    payload = {
        "channel_index": 1,
        "authorized_senders": [
            {"pubkey_prefix": "abcdef123456", "name": "admin", "role": "operator"}
        ],
        "allow_unidentified_readonly_testing": False,
    }
    options_file.write_text(json.dumps(payload), encoding="utf-8")

    options = HomeAssistantAppOptions.from_file(str(options_file))

    serialized = json.loads(options_file.read_text(encoding="utf-8"))
    assert serialized["authorized_senders"] == payload["authorized_senders"]
    assert options.authorized_senders[0].role is Role.operator


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


def test_homeassistant_app_testing_mode_can_start_with_missing_authorized_senders() -> None:
    options = HomeAssistantAppOptions.from_mapping(
        {
            "channel_index": 1,
            "allow_unidentified_readonly_testing": True,
        }
    )

    assert options.authorized_senders == ()
    assert options.allow_unidentified_readonly_testing is True


def test_homeassistant_app_testing_mode_disabled_rejects_empty_authorized_senders() -> None:
    with pytest.raises(ValueError, match="at least one authorized sender"):
        HomeAssistantAppOptions.from_mapping(
            {
                "channel_index": 1,
                "authorized_senders": [],
                "allow_unidentified_readonly_testing": False,
            }
        )


def test_homeassistant_app_upgrade_from_0_1_11_options_keeps_authorized_senders() -> None:
    options = HomeAssistantAppOptions.from_mapping(
        {
            "channel_index": 1,
            "meshcore_entry_id": "",
            "command_prefix": "!",
            "authorized_senders": [
                {"pubkey_prefix": "abcdef123456", "name": "admin", "role": "admin"}
            ],
            "status_entities": [],
            "weather_status": {
                "temperature_entity": "",
                "humidity_entity": "",
                "label": "Exterior",
            },
            "telegram": {
                "enabled": True,
                "bot_token_import": "",
                "bot_token_file": "/data/telegram.bot_token",
                "allowed_private_chat_id": "1001",
                "allowed_user_id": "2002",
                "meshcore_channel_index": 1,
                "forward_meshcore_to_telegram": True,
                "forward_telegram_to_meshcore": True,
                "command_prefix": "!",
                "max_meshcore_message_length": 180,
                "message_prefix": "TG: ",
                "forwarding_rate_limit": {"messages": 5, "window_seconds": 60},
            },
            "rate_limit": {"commands": 5, "window_seconds": 60},
            "log_level": "info",
            "allow_unidentified_readonly_testing": False,
        }
    )

    assert options.authorized_senders[0].pubkey_prefix == "abcdef123456"
    assert options.telegram.enabled is True
    assert options.telegram.max_telegram_message_length == 3900
    assert options.telegram.send_forward_confirmation is False


def test_homeassistant_app_upgrade_from_0_1_12_options_keeps_authorized_senders() -> None:
    options = HomeAssistantAppOptions.from_mapping(
        {
            "channel_index": 1,
            "authorized_senders": [
                {"pubkey_prefix": "abcdef123456", "name": "admin", "role": "readonly"}
            ],
            "telegram": {
                "enabled": True,
                "bot_token_import": "",
                "bot_token_file": "/data/telegram.bot_token",
                "allowed_private_chat_id": "1001",
                "allowed_user_id": "2002",
                "meshcore_channel_index": 1,
                "forward_meshcore_to_telegram": True,
                "forward_telegram_to_meshcore": True,
                "command_prefix": "!",
                "max_meshcore_message_length": 180,
                "max_telegram_message_length": 3900,
                "message_prefix": "TG: ",
                "meshcore_to_telegram_prefix": "MC: ",
                "forwarding_rate_limit": {"messages": 5, "window_seconds": 60},
                "inbound_forwarding_rate_limit": {"messages": 20, "window_seconds": 60},
            },
        }
    )

    assert options.authorized_senders[0].role is Role.readonly
    assert options.telegram.allowed_private_chat_id == "1001"
    assert options.telegram.send_forward_confirmation is False


def test_homeassistant_app_telegram_does_not_replace_meshcore_authorized_senders() -> None:
    runtime = HomeAssistantRuntime(
        rest_base_url=SUPERVISOR_REST_BASE_URL,
        websocket_url=SUPERVISOR_WEBSOCKET_URL,
        token="supervisor-token-not-real",
    )
    options = HomeAssistantAppOptions.from_mapping(
        {
            "channel_index": 1,
            "authorized_senders": [
                {"pubkey_prefix": "abcdef123456", "name": "admin", "role": "readonly"}
            ],
            "telegram": {
                "enabled": True,
                "allowed_private_chat_id": "1001",
                "allowed_user_id": "2002",
            },
        }
    )

    config = options.to_app_config(runtime)

    assert "meshcore-pubkey-prefix:abcdef123456" in config.users
    assert "telegram-user:authorized" not in config.users
    assert config.telegram.enabled is True


def test_homeassistant_app_healthcheck_accepts_recent_file(tmp_path, monkeypatch) -> None:
    health_file = tmp_path / "health.json"
    health_file.write_text(
        json.dumps({"status": "ok", "updated_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(homeassistant_app_health, "APP_HEALTHCHECK_PATH", str(health_file))

    homeassistant_app_health.main()
