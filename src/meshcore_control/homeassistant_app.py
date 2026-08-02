from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from meshcore_control.auth.authorization import AuthorizedUser, RoomPolicy
from meshcore_control.auth.roles import Role, parse_role
from meshcore_control.config import (
    AppConfig,
    HomeAssistantConfig,
    MeshCoreConfig,
    RateLimitConfig,
    SecurityConfig,
    StatusEntityConfig,
)

SUPERVISOR_REST_BASE_URL = "http://supervisor/core"
SUPERVISOR_WEBSOCKET_URL = "ws://supervisor/core/websocket"
APP_OPTIONS_PATH = "/data/options.json"
APP_DATABASE_PATH = "/data/audit.db"
APP_HEALTHCHECK_PATH = "/data/health.json"


def unidentified_testing_sender_id(channel_index: int) -> str:
    return f"test:unidentified:channel:{channel_index}"


@dataclass(frozen=True, slots=True)
class HomeAssistantRuntime:
    rest_base_url: str
    websocket_url: str
    token: str
    verify_tls: bool = True

    @classmethod
    def from_supervisor_environment(cls) -> HomeAssistantRuntime:
        token = os.getenv("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError("SUPERVISOR_TOKEN is unavailable")
        return cls(
            rest_base_url=SUPERVISOR_REST_BASE_URL,
            websocket_url=SUPERVISOR_WEBSOCKET_URL,
            token=token,
            verify_tls=True,
        )


@dataclass(frozen=True, slots=True)
class AppAuthorizedSender:
    pubkey_prefix: str
    name: str
    role: Role


@dataclass(frozen=True, slots=True)
class AppStatusEntity:
    alias: str
    entity_id: str
    label: str


@dataclass(frozen=True, slots=True)
class HomeAssistantAppOptions:
    channel_index: int = 1
    meshcore_entry_id: str = ""
    command_prefix: str = "!"
    authorized_senders: tuple[AppAuthorizedSender, ...] = ()
    status_entities: tuple[AppStatusEntity, ...] = ()
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    log_level: str = "info"
    allow_unidentified_readonly_testing: bool = False

    @classmethod
    def from_file(cls, path: str = APP_OPTIONS_PATH) -> HomeAssistantAppOptions:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("options.json root must be an object")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> HomeAssistantAppOptions:
        channel_index = _int(payload.get("channel_index", 1), "channel_index")
        if channel_index == 0:
            raise ValueError("channel_index 0/Public must not be used for administration")
        if channel_index < 0:
            raise ValueError("channel_index must be positive")

        log_level = str(payload.get("log_level", "info")).lower()
        if log_level not in {"debug", "info", "warning", "error"}:
            raise ValueError("log_level must be one of debug, info, warning, error")

        rate_limit_data = _mapping(payload.get("rate_limit", {}), "rate_limit")
        options = cls(
            channel_index=channel_index,
            meshcore_entry_id=str(payload.get("meshcore_entry_id", "") or ""),
            command_prefix=str(payload.get("command_prefix", "!") or "!"),
            authorized_senders=_parse_authorized_senders(payload.get("authorized_senders", [])),
            status_entities=_parse_status_entities(payload.get("status_entities", [])),
            rate_limit=RateLimitConfig(
                commands=_int(rate_limit_data.get("commands", 5), "rate_limit.commands"),
                window_seconds=_int(
                    rate_limit_data.get("window_seconds", 60),
                    "rate_limit.window_seconds",
                ),
            ),
            log_level=log_level,
            allow_unidentified_readonly_testing=bool(
                payload.get("allow_unidentified_readonly_testing", False)
            ),
        )
        if options.rate_limit.commands < 1:
            raise ValueError("rate_limit.commands must be positive")
        if options.rate_limit.window_seconds < 1:
            raise ValueError("rate_limit.window_seconds must be positive")
        if not options.authorized_senders and not options.allow_unidentified_readonly_testing:
            raise ValueError("at least one authorized sender is required")
        return options

    def to_app_config(self, runtime: HomeAssistantRuntime) -> AppConfig:
        users = {
            _sender_id(sender.pubkey_prefix): AuthorizedUser(
                sender_id=_sender_id(sender.pubkey_prefix),
                name=sender.name,
                role=sender.role,
            )
            for sender in self.authorized_senders
        }
        if self.allow_unidentified_readonly_testing:
            users[unidentified_testing_sender_id(self.channel_index)] = AuthorizedUser(
                sender_id=unidentified_testing_sender_id(self.channel_index),
                name="unidentified-channel-testing",
                role=Role.readonly,
            )
        room_id = f"homeassistant-meshcore:channel:{self.channel_index}"
        return AppConfig(
            command_prefix=self.command_prefix,
            database_path=APP_DATABASE_PATH,
            meshcore=MeshCoreConfig(
                transport="homeassistant",
                channel_index=self.channel_index,
                ha_entry_id=self.meshcore_entry_id or None,
                event_types=("meshcore_message",),
                require_stable_sender=not self.allow_unidentified_readonly_testing,
                allow_channel_without_sender=self.allow_unidentified_readonly_testing,
                healthcheck_path=APP_HEALTHCHECK_PATH,
            ),
            homeassistant=HomeAssistantConfig(
                base_url=runtime.rest_base_url,
                token=runtime.token,
                verify_tls=runtime.verify_tls,
                timeout_seconds=5,
                websocket_url=runtime.websocket_url,
            ),
            users=users,
            room_policies={
                room_id: RoomPolicy(
                    room_id=room_id,
                    enabled=True,
                    minimum_role=Role.readonly,
                    allow_commands=True,
                )
            },
            status_entities={
                entity.alias: StatusEntityConfig(entity_id=entity.entity_id, label=entity.label)
                for entity in self.status_entities
            },
            security=SecurityConfig(rate_limit=self.rate_limit),
        )


def load_homeassistant_app_config(
    options_path: str = APP_OPTIONS_PATH,
    runtime: HomeAssistantRuntime | None = None,
) -> tuple[AppConfig, HomeAssistantAppOptions]:
    runtime = runtime or HomeAssistantRuntime.from_supervisor_environment()
    options = HomeAssistantAppOptions.from_file(options_path)
    return options.to_app_config(runtime), options


def _parse_authorized_senders(value: object) -> tuple[AppAuthorizedSender, ...]:
    if not isinstance(value, list):
        raise ValueError("authorized_senders must be a list")
    senders: list[AppAuthorizedSender] = []
    for item in value:
        data = _mapping(item, "authorized_senders item")
        pubkey_prefix = str(data.get("pubkey_prefix", "")).strip()
        if len(pubkey_prefix) < 6:
            raise ValueError("authorized sender pubkey_prefix must be at least 6 characters")
        role = parse_role(str(data.get("role", "readonly")))
        senders.append(
            AppAuthorizedSender(
                pubkey_prefix=pubkey_prefix,
                name=str(data.get("name", pubkey_prefix)),
                role=role,
            )
        )
    return tuple(senders)


def _parse_status_entities(value: object) -> tuple[AppStatusEntity, ...]:
    if not isinstance(value, list):
        raise ValueError("status_entities must be a list")
    entities: list[AppStatusEntity] = []
    for item in value:
        data = _mapping(item, "status_entities item")
        alias = str(data.get("alias", "")).strip()
        entity_id = str(data.get("entity_id", "")).strip()
        label = str(data.get("label", alias)).strip()
        if not alias:
            raise ValueError("status entity alias is required")
        if not entity_id:
            raise ValueError("status entity entity_id is required")
        entities.append(AppStatusEntity(alias=alias, entity_id=entity_id, label=label or alias))
    return tuple(entities)


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return dict(value)


def _int(value: object, name: str) -> int:
    try:
        if isinstance(value, str | int | float):
            return int(value)
        raise TypeError
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _sender_id(pubkey_prefix: str) -> str:
    normalized = pubkey_prefix.strip().lower()
    if normalized.startswith("meshcore-pubkey-prefix:"):
        return normalized
    return f"meshcore-pubkey-prefix:{normalized}"
