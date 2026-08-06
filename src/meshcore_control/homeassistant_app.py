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
    HealthConfig,
    HomeAssistantConfig,
    MeshCoreConfig,
    RateLimitConfig,
    SecurityConfig,
    StatusEntityConfig,
    TelegramConfig,
    WeatherStatusConfig,
    validate_weather_status_label,
)

SUPERVISOR_REST_BASE_URL = "http://supervisor/core"
SUPERVISOR_WEBSOCKET_URL = "ws://supervisor/core/websocket"
APP_OPTIONS_PATH = "/data/options.json"
APP_DATABASE_PATH = "/data/audit.db"
APP_HEALTHCHECK_PATH = "/data/health.json"
_MISSING = object()


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
class AppWeatherStatus:
    temperature_entity: str = ""
    humidity_entity: str = ""
    label: str = "Exterior"


@dataclass(frozen=True, slots=True)
class AppTelegramOptions:
    enabled: bool = False
    bot_token_import: str = ""
    bot_token_file: str = "/data/telegram.bot_token"
    allowed_private_chat_id: str = ""
    allowed_user_id: str = ""
    authorized_user_role: Role = Role.readonly
    meshcore_channel_index: int = 1
    forward_meshcore_to_telegram: bool = True
    forward_telegram_to_meshcore: bool = True
    command_prefix: str = "!"
    max_meshcore_message_length: int = 180
    max_telegram_message_length: int = 3900
    message_prefix: str = "TG: "
    meshcore_to_telegram_prefix: str = "MC: "
    send_forward_confirmation: bool = False
    forwarding_rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    inbound_forwarding_rate_limit: RateLimitConfig = field(
        default_factory=lambda: RateLimitConfig(commands=20, window_seconds=60)
    )


@dataclass(frozen=True, slots=True)
class AppHealthOptions:
    home_assistant_events_enabled: bool = True
    heartbeat_seconds: int = 60


@dataclass(frozen=True, slots=True)
class HomeAssistantAppOptions:
    channel_index: int = 1
    meshcore_entry_id: str = ""
    command_prefix: str = "!"
    authorized_senders: tuple[AppAuthorizedSender, ...] = ()
    status_entities: tuple[AppStatusEntity, ...] = ()
    weather_status: AppWeatherStatus = field(default_factory=AppWeatherStatus)
    telegram: AppTelegramOptions = field(default_factory=AppTelegramOptions)
    health: AppHealthOptions = field(default_factory=AppHealthOptions)
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
        allow_unidentified_readonly_testing = bool(
            payload.get("allow_unidentified_readonly_testing", False)
        )
        authorized_senders_value = payload.get("authorized_senders", _MISSING)
        options = cls(
            channel_index=channel_index,
            meshcore_entry_id=str(payload.get("meshcore_entry_id", "") or ""),
            command_prefix=str(payload.get("command_prefix", "!") or "!"),
            authorized_senders=_parse_authorized_senders(authorized_senders_value),
            status_entities=_parse_status_entities(payload.get("status_entities", [])),
            weather_status=_parse_weather_status(payload.get("weather_status", {})),
            telegram=_parse_telegram(payload.get("telegram", {})),
            health=_parse_health(payload.get("health", {})),
            rate_limit=RateLimitConfig(
                commands=_int(rate_limit_data.get("commands", 5), "rate_limit.commands"),
                window_seconds=_int(
                    rate_limit_data.get("window_seconds", 60),
                    "rate_limit.window_seconds",
                ),
            ),
            log_level=log_level,
            allow_unidentified_readonly_testing=allow_unidentified_readonly_testing,
        )
        if options.rate_limit.commands < 1:
            raise ValueError("rate_limit.commands must be positive")
        if options.rate_limit.window_seconds < 1:
            raise ValueError("rate_limit.window_seconds must be positive")
        if not options.authorized_senders and not options.allow_unidentified_readonly_testing:
            if authorized_senders_value is _MISSING:
                raise ValueError(
                    "authorized_senders is missing; configure at least one MeshCore "
                    "authorized sender or enable unidentified readonly testing temporarily"
                )
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
            weather_status=WeatherStatusConfig(
                temperature_entity=self.weather_status.temperature_entity,
                humidity_entity=self.weather_status.humidity_entity,
                label=self.weather_status.label,
            ),
            telegram=TelegramConfig(
                enabled=self.telegram.enabled,
                bot_token_import=self.telegram.bot_token_import,
                bot_token_file=self.telegram.bot_token_file,
                allowed_private_chat_id=self.telegram.allowed_private_chat_id,
                allowed_user_id=self.telegram.allowed_user_id,
                authorized_user_role=self.telegram.authorized_user_role,
                meshcore_channel_index=self.telegram.meshcore_channel_index,
                forward_meshcore_to_telegram=self.telegram.forward_meshcore_to_telegram,
                forward_telegram_to_meshcore=self.telegram.forward_telegram_to_meshcore,
                command_prefix=self.telegram.command_prefix,
                max_meshcore_message_length=self.telegram.max_meshcore_message_length,
                max_telegram_message_length=self.telegram.max_telegram_message_length,
                message_prefix=self.telegram.message_prefix,
                meshcore_to_telegram_prefix=self.telegram.meshcore_to_telegram_prefix,
                send_forward_confirmation=self.telegram.send_forward_confirmation,
                forwarding_rate_limit=self.telegram.forwarding_rate_limit,
                inbound_forwarding_rate_limit=self.telegram.inbound_forwarding_rate_limit,
            ),
            health=HealthConfig(
                home_assistant_events_enabled=self.health.home_assistant_events_enabled,
                heartbeat_seconds=self.health.heartbeat_seconds,
            ),
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
    if value is _MISSING:
        return ()
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


def _parse_weather_status(value: object) -> AppWeatherStatus:
    if value in (None, ""):
        return AppWeatherStatus()
    data = _mapping(value, "weather_status")
    return AppWeatherStatus(
        temperature_entity=str(data.get("temperature_entity", "") or "").strip(),
        humidity_entity=str(data.get("humidity_entity", "") or "").strip(),
        label=validate_weather_status_label(data.get("label", "Exterior")),
    )


def _parse_telegram(value: object) -> AppTelegramOptions:
    if value in (None, ""):
        return AppTelegramOptions()
    data = _mapping(value, "telegram")
    meshcore_channel_index = _int(
        data.get("meshcore_channel_index", 1),
        "telegram.meshcore_channel_index",
    )
    if meshcore_channel_index == 0:
        raise ValueError("telegram.meshcore_channel_index 0/Public must not be used")
    if meshcore_channel_index < 0:
        raise ValueError("telegram.meshcore_channel_index must be positive")
    max_meshcore_message_length = _int(
        data.get("max_meshcore_message_length", 180),
        "telegram.max_meshcore_message_length",
    )
    if max_meshcore_message_length < 1:
        raise ValueError("telegram.max_meshcore_message_length must be positive")
    max_telegram_message_length = _int(
        data.get("max_telegram_message_length", 3900),
        "telegram.max_telegram_message_length",
    )
    if max_telegram_message_length < 1:
        raise ValueError("telegram.max_telegram_message_length must be positive")
    command_prefix = str(data.get("command_prefix", "!") or "!")
    if not command_prefix:
        raise ValueError("telegram.command_prefix must not be empty")
    message_prefix = _validate_telegram_message_prefix(
        data.get("message_prefix", "TG: "),
        "telegram.message_prefix",
    )
    meshcore_to_telegram_prefix = _validate_telegram_message_prefix(
        data.get("meshcore_to_telegram_prefix", "MC: "),
        "telegram.meshcore_to_telegram_prefix",
    )
    forwarding_rate_limit_data = _mapping(
        data.get("forwarding_rate_limit", {}),
        "telegram.forwarding_rate_limit",
    )
    forwarding_rate_limit = RateLimitConfig(
        commands=_int(
            forwarding_rate_limit_data.get("messages", 5),
            "telegram.forwarding_rate_limit.messages",
        ),
        window_seconds=_int(
            forwarding_rate_limit_data.get("window_seconds", 60),
            "telegram.forwarding_rate_limit.window_seconds",
        ),
    )
    if forwarding_rate_limit.commands < 1:
        raise ValueError("telegram.forwarding_rate_limit.messages must be positive")
    if forwarding_rate_limit.window_seconds < 1:
        raise ValueError("telegram.forwarding_rate_limit.window_seconds must be positive")
    inbound_forwarding_rate_limit_data = _mapping(
        data.get("inbound_forwarding_rate_limit", {}),
        "telegram.inbound_forwarding_rate_limit",
    )
    inbound_forwarding_rate_limit = RateLimitConfig(
        commands=_int(
            inbound_forwarding_rate_limit_data.get("messages", 20),
            "telegram.inbound_forwarding_rate_limit.messages",
        ),
        window_seconds=_int(
            inbound_forwarding_rate_limit_data.get("window_seconds", 60),
            "telegram.inbound_forwarding_rate_limit.window_seconds",
        ),
    )
    if inbound_forwarding_rate_limit.commands < 1:
        raise ValueError("telegram.inbound_forwarding_rate_limit.messages must be positive")
    if inbound_forwarding_rate_limit.window_seconds < 1:
        raise ValueError("telegram.inbound_forwarding_rate_limit.window_seconds must be positive")
    options = AppTelegramOptions(
        enabled=bool(data.get("enabled", False)),
        bot_token_import=str(data.get("bot_token_import", "") or ""),
        bot_token_file=str(data.get("bot_token_file", "/data/telegram.bot_token") or ""),
        allowed_private_chat_id=str(data.get("allowed_private_chat_id", "") or "").strip(),
        allowed_user_id=str(data.get("allowed_user_id", "") or "").strip(),
        authorized_user_role=parse_role(str(data.get("authorized_user_role", "readonly"))),
        meshcore_channel_index=meshcore_channel_index,
        forward_meshcore_to_telegram=bool(data.get("forward_meshcore_to_telegram", True)),
        forward_telegram_to_meshcore=bool(data.get("forward_telegram_to_meshcore", True)),
        command_prefix=command_prefix,
        max_meshcore_message_length=max_meshcore_message_length,
        max_telegram_message_length=max_telegram_message_length,
        message_prefix=message_prefix,
        meshcore_to_telegram_prefix=meshcore_to_telegram_prefix,
        send_forward_confirmation=_bool(
            data.get("send_forward_confirmation", False),
            "telegram.send_forward_confirmation",
        ),
        forwarding_rate_limit=forwarding_rate_limit,
        inbound_forwarding_rate_limit=inbound_forwarding_rate_limit,
    )
    if options.enabled:
        if not options.allowed_private_chat_id:
            raise ValueError("telegram.allowed_private_chat_id is required when enabled")
        if not options.allowed_user_id:
            raise ValueError("telegram.allowed_user_id is required when enabled")
        if not options.bot_token_file:
            raise ValueError("telegram.bot_token_file is required when enabled")
    return options


def _parse_health(value: object) -> AppHealthOptions:
    if value in (None, ""):
        return AppHealthOptions()
    data = _mapping(value, "health")
    heartbeat_seconds = _int(data.get("heartbeat_seconds", 60), "health.heartbeat_seconds")
    if not 15 <= heartbeat_seconds <= 3600:
        raise ValueError("health.heartbeat_seconds must be between 15 and 3600")
    return AppHealthOptions(
        home_assistant_events_enabled=_bool(
            data.get("home_assistant_events_enabled", True),
            "health.home_assistant_events_enabled",
        ),
        heartbeat_seconds=heartbeat_seconds,
    )


def _validate_telegram_message_prefix(value: object, field_name: str) -> str:
    prefix = str(value or "")
    if any(character in prefix for character in ("\n", "\r")):
        raise ValueError(f"{field_name} must not contain newlines")
    if any(ord(character) < 32 or ord(character) == 127 for character in prefix):
        raise ValueError(f"{field_name} must not contain control characters")
    if len(prefix) > 16:
        raise ValueError(f"{field_name} must be 16 characters or fewer")
    return prefix


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


def _bool(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be a boolean")


def _sender_id(pubkey_prefix: str) -> str:
    normalized = pubkey_prefix.strip().lower()
    if normalized.startswith("meshcore-pubkey-prefix:"):
        return normalized
    return f"meshcore-pubkey-prefix:{normalized}"
