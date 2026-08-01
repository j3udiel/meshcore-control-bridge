from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from meshcore_control.auth.authorization import AuthorizedUser
from meshcore_control.auth.roles import parse_role


@dataclass(frozen=True, slots=True)
class HomeAssistantConfig:
    base_url: str
    token: str
    verify_tls: bool = True
    timeout_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class MeshCoreConfig:
    channel_index: int = 1
    serial_port: str | None = None
    baudrate: int = 115200


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
    entities: dict[str, dict[str, str]] = field(default_factory=dict)
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_config(config_path: str | None = None) -> AppConfig:
    file_data = _read_yaml(config_path) if config_path else {}

    meshcore_data = dict(file_data.get("meshcore", {}))
    ha_data = dict(file_data.get("homeassistant", {}))

    meshcore = MeshCoreConfig(
        channel_index=_env_int("MESHCORE_CHANNEL_INDEX", meshcore_data.get("channel_index", 1)),
        serial_port=os.getenv("MESHCORE_SERIAL_PORT", meshcore_data.get("serial_port")),
        baudrate=_env_int("MESHCORE_BAUDRATE", meshcore_data.get("baudrate", 115200)),
    )
    homeassistant = HomeAssistantConfig(
        base_url=os.getenv("HA_BASE_URL", ha_data.get("base_url", "")).rstrip("/"),
        token=os.getenv("HA_TOKEN", ha_data.get("token", "")),
        verify_tls=_env_bool("HA_VERIFY_TLS", ha_data.get("verify_tls", True)),
        timeout_seconds=_env_float("HA_TIMEOUT_SECONDS", ha_data.get("timeout_seconds", 5.0)),
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
        entities=dict(file_data.get("entities", {})),
        servers=dict(file_data.get("servers", {})),
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


def _env_bool(name: str, default: Any) -> bool:
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: Any) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: Any) -> float:
    return float(os.getenv(name, str(default)))
