from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from meshcore_control.auth.authorization import AuthorizedUser, RoomPolicy
from meshcore_control.auth.roles import Role, parse_role

WEATHER_STATUS_DEFAULT_LABEL = "Exterior"
WEATHER_STATUS_LABEL_MAX_LENGTH = 32


@dataclass(frozen=True, slots=True)
class HomeAssistantConfig:
    base_url: str
    token: str
    verify_tls: bool = True
    timeout_seconds: float = 5.0
    websocket_url: str | None = None


@dataclass(frozen=True, slots=True)
class MeshCoreConfig:
    transport: str = "placeholder"
    channel_index: int = 1
    serial_port: str | None = None
    baudrate: int = 115200
    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    protocol_version: str = "auto"
    ha_entry_id: str | None = None
    ha_device_id: str | None = None
    event_types: tuple[str, ...] = ("meshcore_message",)
    require_stable_sender: bool = True
    allow_channel_without_sender: bool = False
    healthcheck_path: str | None = None


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    commands: int = 5
    window_seconds: int = 60


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)


@dataclass(frozen=True, slots=True)
class StatusEntityConfig:
    entity_id: str
    label: str


@dataclass(frozen=True, slots=True)
class WeatherStatusConfig:
    temperature_entity: str = ""
    humidity_entity: str = ""
    label: str = WEATHER_STATUS_DEFAULT_LABEL


@dataclass(frozen=True, slots=True)
class AppConfig:
    command_prefix: str = "!"
    database_path: str = "data/audit.db"
    deduplication_window_seconds: int = 300
    audit_retention_days: int = 30
    meshcore: MeshCoreConfig = field(default_factory=MeshCoreConfig)
    homeassistant: HomeAssistantConfig = field(
        default_factory=lambda: HomeAssistantConfig(base_url="", token="")
    )
    users: dict[str, AuthorizedUser] = field(default_factory=dict)
    room_policies: dict[str, RoomPolicy] = field(default_factory=dict)
    entities: dict[str, dict[str, str]] = field(default_factory=dict)
    status_entities: dict[str, StatusEntityConfig] = field(default_factory=dict)
    weather_status: WeatherStatusConfig = field(default_factory=WeatherStatusConfig)
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    security: SecurityConfig = field(default_factory=SecurityConfig)


def load_config(config_path: str | None = None) -> AppConfig:
    file_data = _read_yaml(config_path) if config_path else {}

    meshcore_data = dict(file_data.get("meshcore", {}))
    ha_data = dict(file_data.get("homeassistant", {}))

    meshcore = MeshCoreConfig(
        transport=str(
            os.getenv("MESHCORE_TRANSPORT", meshcore_data.get("transport", "placeholder"))
        ),
        channel_index=_env_int("MESHCORE_CHANNEL_INDEX", meshcore_data.get("channel_index", 1)),
        serial_port=os.getenv("MESHCORE_SERIAL_PORT", meshcore_data.get("serial_port")),
        baudrate=_env_int("MESHCORE_BAUDRATE", meshcore_data.get("baudrate", 115200)),
        reconnect_initial_seconds=_env_float(
            "MESHCORE_RECONNECT_INITIAL_SECONDS",
            meshcore_data.get("reconnect_initial_seconds", 1.0),
        ),
        reconnect_max_seconds=_env_float(
            "MESHCORE_RECONNECT_MAX_SECONDS",
            meshcore_data.get("reconnect_max_seconds", 30.0),
        ),
        protocol_version=str(
            os.getenv("MESHCORE_PROTOCOL_VERSION", meshcore_data.get("protocol_version", "auto"))
        ),
        ha_entry_id=os.getenv("MESHCORE_HA_ENTRY_ID", meshcore_data.get("ha_entry_id")),
        ha_device_id=os.getenv("MESHCORE_HA_DEVICE_ID", meshcore_data.get("ha_device_id")),
        event_types=tuple(meshcore_data.get("event_types", ["meshcore_message"])),
        require_stable_sender=_env_bool(
            "MESHCORE_REQUIRE_STABLE_SENDER",
            meshcore_data.get("require_stable_sender", True),
        ),
        allow_channel_without_sender=_env_bool(
            "MESHCORE_ALLOW_CHANNEL_WITHOUT_SENDER",
            meshcore_data.get("allow_channel_without_sender", False),
        ),
    )
    homeassistant = HomeAssistantConfig(
        base_url=os.getenv("HA_BASE_URL", ha_data.get("base_url", "")).rstrip("/"),
        token=os.getenv("HA_TOKEN", ha_data.get("token", "")),
        verify_tls=_env_bool("HA_VERIFY_TLS", ha_data.get("verify_tls", True)),
        timeout_seconds=_env_float("HA_TIMEOUT_SECONDS", ha_data.get("timeout_seconds", 5.0)),
        websocket_url=os.getenv("HA_WEBSOCKET_URL", ha_data.get("websocket_url")),
    )
    config = AppConfig(
        command_prefix=os.getenv("COMMAND_PREFIX", file_data.get("command_prefix", "!")),
        database_path=os.getenv("DATABASE_PATH", file_data.get("database_path", "data/audit.db")),
        deduplication_window_seconds=_env_int(
            "DEDUPLICATION_WINDOW_SECONDS", file_data.get("deduplication_window_seconds", 300)
        ),
        audit_retention_days=_env_int(
            "AUDIT_RETENTION_DAYS", file_data.get("audit_retention_days", 30)
        ),
        meshcore=meshcore,
        homeassistant=homeassistant,
        users=_parse_users(file_data.get("users", {})),
        room_policies=_legacy_room_policies(meshcore),
        entities=dict(file_data.get("entities", {})),
        status_entities=_parse_status_entities(file_data.get("status", {}).get("entities", {})),
        weather_status=_parse_weather_status(file_data.get("weather_status", {})),
        servers=dict(file_data.get("servers", {})),
        security=_parse_security(file_data.get("security", {})),
    )
    _validate(config)
    return config


def _read_yaml(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise ValueError(f"config file does not exist: {config_path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("configuration root must be a mapping")
    return data


def _parse_users(raw_users: dict[str, Any]) -> dict[str, AuthorizedUser]:
    users: dict[str, AuthorizedUser] = {}
    for sender_id, raw_user in raw_users.items():
        if not isinstance(raw_user, dict):
            raise ValueError(f"user {sender_id!r} must be a mapping")
        users[str(sender_id)] = AuthorizedUser(
            sender_id=str(sender_id),
            name=str(raw_user.get("name", sender_id)),
            role=parse_role(str(raw_user.get("role", "readonly"))),
        )
    return users


def _legacy_room_policies(meshcore: MeshCoreConfig) -> dict[str, RoomPolicy]:
    room_id = f"{_transport_room_scope(meshcore.transport)}:channel:{meshcore.channel_index}"
    return {
        room_id: RoomPolicy(
            room_id=room_id,
            enabled=True,
            minimum_role=Role.readonly,
            allow_commands=True,
        )
    }


def _transport_room_scope(transport: str) -> str:
    if transport == "homeassistant":
        return "homeassistant-meshcore"
    if transport == "fake":
        return "fake"
    if transport == "usb":
        return "meshcore-usb"
    return transport


def _parse_status_entities(raw_entities: dict[str, Any]) -> dict[str, StatusEntityConfig]:
    entities: dict[str, StatusEntityConfig] = {}
    for alias, raw_entity in raw_entities.items():
        if not isinstance(raw_entity, dict):
            raise ValueError(f"status entity {alias!r} must be a mapping")
        entity_id = str(raw_entity.get("entity_id", ""))
        label = str(raw_entity.get("label", alias))
        if not entity_id:
            raise ValueError(f"status entity {alias!r} requires entity_id")
        entities[str(alias)] = StatusEntityConfig(entity_id=entity_id, label=label)
    return entities


def _parse_weather_status(raw_weather: dict[str, Any]) -> WeatherStatusConfig:
    if not isinstance(raw_weather, dict):
        raise ValueError("weather_status must be a mapping")
    return WeatherStatusConfig(
        temperature_entity=str(raw_weather.get("temperature_entity", "") or "").strip(),
        humidity_entity=str(raw_weather.get("humidity_entity", "") or "").strip(),
        label=validate_weather_status_label(raw_weather.get("label", WEATHER_STATUS_DEFAULT_LABEL)),
    )


def validate_weather_status_label(value: object) -> str:
    label = str(value or WEATHER_STATUS_DEFAULT_LABEL).strip() or WEATHER_STATUS_DEFAULT_LABEL
    if any(character in label for character in ("\n", "\r")):
        raise ValueError("weather_status.label must not contain newlines")
    if any(ord(character) < 32 or ord(character) == 127 for character in label):
        raise ValueError("weather_status.label must not contain control characters")
    if len(label) > WEATHER_STATUS_LABEL_MAX_LENGTH:
        raise ValueError("weather_status.label must be 32 characters or fewer")
    return label


def _parse_security(raw_security: dict[str, Any]) -> SecurityConfig:
    rate_limit_data = dict(raw_security.get("rate_limit", {}))
    return SecurityConfig(
        rate_limit=RateLimitConfig(
            commands=_env_int(
                "RATE_LIMIT_COMMANDS", rate_limit_data.get("commands", 5)
            ),
            window_seconds=_env_int(
                "RATE_LIMIT_WINDOW_SECONDS", rate_limit_data.get("window_seconds", 60)
            ),
        )
    )


def _validate(config: AppConfig) -> None:
    if config.meshcore.channel_index == 0:
        raise ValueError("MESHCORE_CHANNEL_INDEX must not be 0 for administration")
    if config.meshcore.channel_index < 0:
        raise ValueError("MESHCORE_CHANNEL_INDEX must be positive")
    if not config.command_prefix:
        raise ValueError("command_prefix must not be empty")
    if config.homeassistant.base_url and not config.homeassistant.base_url.startswith(("http://", "https://")):
        raise ValueError("HA_BASE_URL must start with http:// or https://")
    if config.homeassistant.token and len(config.homeassistant.token) < 10:
        raise ValueError("HA_TOKEN looks too short")
    if config.meshcore.transport not in {"placeholder", "usb", "homeassistant", "fake"}:
        raise ValueError("meshcore.transport must be one of: placeholder, usb, homeassistant, fake")
    if config.meshcore.transport == "usb" and not config.meshcore.serial_port:
        raise ValueError("MESHCORE_SERIAL_PORT is required when meshcore.transport is usb")
    if config.meshcore.transport == "homeassistant":
        if not config.homeassistant.base_url:
            raise ValueError("HA_BASE_URL is required when meshcore.transport is homeassistant")
        if not config.homeassistant.token:
            raise ValueError("HA_TOKEN is required when meshcore.transport is homeassistant")
        if "meshcore_message" not in config.meshcore.event_types:
            raise ValueError("meshcore.event_types must include meshcore_message")
    if config.security.rate_limit.commands < 1:
        raise ValueError("rate_limit.commands must be positive")
    if config.security.rate_limit.window_seconds < 1:
        raise ValueError("rate_limit.window_seconds must be positive")


def _env_bool(name: str, default: Any) -> bool:
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: Any) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: Any) -> float:
    return float(os.getenv(name, str(default)))
