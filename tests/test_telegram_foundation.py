from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from meshcore_control.adapters.homeassistant import HomeAssistantStatus
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer, RoomPolicy
from meshcore_control.auth.roles import Role
from meshcore_control.bridge_health import BridgeHealthState
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig, RateLimitConfig, TelegramConfig, WeatherStatusConfig
from meshcore_control.models import OutboundMessage
from meshcore_control.plugins import build_registry
from meshcore_control.storage.audit_flow import AuditFlow
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.normalized_audit import (
    AUDIT_KEY_MIN_BYTES,
    AuditKey,
    NormalizedAuditRepository,
    NormalizedAuditSettings,
)
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.telegram.client import (
    TelegramBotApiClient,
    TelegramConflictError,
    TelegramRateLimitError,
)
from meshcore_control.telegram.identity import TELEGRAM_ROOM_ID, TELEGRAM_SENDER_ID
from meshcore_control.telegram.service import (
    TelegramFoundationService,
    render_meshcore_forward_message,
)
from meshcore_control.telegram.store import TelegramStore
from meshcore_control.telegram.token import (
    TelegramToken,
    TelegramTokenError,
    load_or_import_token,
    validate_bot_token,
)

VALID_TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"
OTHER_TOKEN = "987654321:ABCDEFGHIJKLMNOPQRSTUVWXYZabcde"


@dataclass
class FakeTelegramClient:
    updates: list[list[dict[str, Any]]] = field(default_factory=list)
    rate_limited_once: bool = False
    delete_webhook_calls: list[bool] = field(default_factory=list)
    get_updates_calls: list[dict[str, Any]] = field(default_factory=list)
    send_message_calls: list[dict[str, str]] = field(default_factory=list)
    send_error: Exception | None = None

    async def delete_webhook(self, *, drop_pending_updates: bool) -> None:
        self.delete_webhook_calls.append(drop_pending_updates)

    async def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        allowed_updates: tuple[str, ...] = ("message",),
    ) -> list[dict[str, Any]]:
        self.get_updates_calls.append(
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": allowed_updates,
            }
        )
        if self.rate_limited_once:
            self.rate_limited_once = False
            raise TelegramRateLimitError(2)
        return self.updates.pop(0) if self.updates else []

    async def send_message(self, *, chat_id: str, text: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.send_message_calls.append({"chat_id": chat_id, "text": text})


@dataclass
class FakeMeshCoreTransport:
    sent: list[OutboundMessage] = field(default_factory=list)
    send_error: Exception | None = None

    async def receive(self):
        raise NotImplementedError

    async def send(self, message: OutboundMessage) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(message)


@dataclass(slots=True)
class FakeHA:
    available: bool = True
    states: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def check_available(self) -> HomeAssistantStatus:
        message = "OK" if self.available else "down"
        return HomeAssistantStatus(available=self.available, message=message)

    async def get_config(self) -> dict[str, object]:
        return {"version": "2026.8.0", "location_name": "Home"}

    async def get_state(self, entity_or_alias: str) -> dict[str, Any]:
        if entity_or_alias not in self.states:
            raise KeyError(entity_or_alias)
        return self.states[entity_or_alias]


def _config(**overrides: object) -> TelegramConfig:
    data = {
        "enabled": True,
        "bot_token_file": "/data/telegram.bot_token",
        "allowed_private_chat_id": "1001",
        "allowed_user_id": "2002",
        "meshcore_channel_index": 1,
        "message_prefix": "TG: ",
    }
    data.update(overrides)
    return TelegramConfig(**data)


def _message(
    update_id: int,
    *,
    chat_id: int = 1001,
    user_id: int = 2002,
    chat_type: str = "private",
    text: str | None = "hello",
    is_bot: bool = False,
    media: bool = False,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": 3003,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": user_id, "is_bot": is_bot},
    }
    if text is not None:
        message["text"] = text
    if media:
        message["photo"] = [{"file_id": "redacted"}]
    return {"update_id": update_id, "message": message}


def _store(tmp_path: Path) -> TelegramStore:
    connection = connect_database(str(tmp_path / "audit.db"))
    return TelegramStore(connection, audit_key=AuditKey(b"a" * 32))


def _command_service(
    tmp_path: Path,
    *,
    client: FakeTelegramClient | None = None,
    ha: FakeHA | None = None,
    weather_status: WeatherStatusConfig | None = None,
    authorized: bool = True,
    meshcore_transport: FakeMeshCoreTransport | None = None,
    config: TelegramConfig | None = None,
    health: BridgeHealthState | None = None,
) -> tuple[TelegramFoundationService, FakeTelegramClient, sqlite3.Connection]:
    connection = connect_database(str(tmp_path / "audit.db"))
    registry = build_registry()
    legacy = AuditRepository(connection)
    audit_flow = AuditFlow(
        connection=connection,
        legacy=legacy,
        normalized=NormalizedAuditRepository(
            connection,
            NormalizedAuditSettings(
                enabled=True,
                audit_key=AuditKey(key=b"t" * AUDIT_KEY_MIN_BYTES, key_id="telegram-key"),
            ),
        ),
    )
    users = (
        {
            TELEGRAM_SENDER_ID: AuthorizedUser(
                TELEGRAM_SENDER_ID,
                "telegram-authorized-user",
                Role.readonly,
            )
        }
        if authorized
        else {}
    )
    telegram_config = config or _config()
    app_config = AppConfig(
        weather_status=weather_status or WeatherStatusConfig(),
        telegram=telegram_config,
    )
    services: dict[str, object] = {"registry": registry, "config": app_config}
    if health is not None:
        services["bridge_health"] = health
    if ha is not None:
        services["homeassistant"] = ha
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(
            users,
            room_policies={
                TELEGRAM_ROOM_ID: RoomPolicy(
                    room_id=TELEGRAM_ROOM_ID,
                    enabled=True,
                    minimum_role=Role.readonly,
                    allow_commands=True,
                )
            },
        ),
        audit=legacy,
        audit_flow=audit_flow,
        services=services,
        prefix="!",
    )
    fake_client = client or FakeTelegramClient()
    service = TelegramFoundationService(
        config=telegram_config,
        client=fake_client,
        store=TelegramStore(connection, audit_key=AuditKey(b"a" * 32)),
        router=router,
        audit_flow=audit_flow,
        meshcore_transport=meshcore_transport,
        normalized_audit=audit_flow.normalized,
        bridge_health=health,
        backoff_max_seconds=1,
        sleep=_noop_sleep,
    )
    return service, fake_client, connection


async def _noop_sleep(delay: float) -> None:
    return None


def _state(value: str, unit: str) -> dict[str, Any]:
    return {"state": value, "attributes": {"unit_of_measurement": unit}}


def test_telegram_disabled_default() -> None:
    assert TelegramConfig().enabled is False


def test_token_import_initial_and_reuse(tmp_path: Path) -> None:
    token_file = tmp_path / "telegram.bot_token"

    token = load_or_import_token(token_import=VALID_TOKEN, token_file=str(token_file))
    reused = load_or_import_token(token_import="", token_file=str(token_file))

    assert token.value == VALID_TOKEN
    assert reused.value == VALID_TOKEN
    assert oct(token_file.stat().st_mode & 0o777) == "0o600"


def test_empty_token_import_does_not_rotate_existing_file(tmp_path: Path) -> None:
    token_file = tmp_path / "telegram.bot_token"
    load_or_import_token(token_import=VALID_TOKEN, token_file=str(token_file))
    first_stat = token_file.stat()

    reused = load_or_import_token(token_import="", token_file=str(token_file))
    second_stat = token_file.stat()

    assert reused.value == VALID_TOKEN
    assert second_stat.st_ino == first_stat.st_ino
    assert second_stat.st_mtime_ns == first_stat.st_mtime_ns


def test_token_rotation(tmp_path: Path) -> None:
    token_file = tmp_path / "telegram.bot_token"

    load_or_import_token(token_import=VALID_TOKEN, token_file=str(token_file))
    rotated = load_or_import_token(token_import=OTHER_TOKEN, token_file=str(token_file))

    assert rotated.value == OTHER_TOKEN
    assert token_file.read_text(encoding="utf-8").strip() == OTHER_TOKEN


def test_token_invalid() -> None:
    with pytest.raises(TelegramTokenError, match="format"):
        validate_bot_token("not-a-token")


def test_enabled_without_token_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(TelegramTokenError, match="does not exist"):
        load_or_import_token(
            token_import="",
            token_file=str(tmp_path / "missing.telegram.bot_token"),
        )


def test_token_symlink_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text(VALID_TOKEN, encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "telegram.bot_token"
    link.symlink_to(target)

    with pytest.raises(TelegramTokenError):
        load_or_import_token(token_import="", token_file=str(link))


def test_token_insecure_permissions_rejected(tmp_path: Path) -> None:
    token_file = tmp_path / "telegram.bot_token"
    token_file.write_text(VALID_TOKEN, encoding="utf-8")
    token_file.chmod(0o644)

    with pytest.raises(TelegramTokenError, match="permissions"):
        load_or_import_token(token_import="", token_file=str(token_file))


@pytest.mark.asyncio
async def test_first_activation_deletes_webhook_and_drops_pending_updates(tmp_path: Path) -> None:
    client = FakeTelegramClient()
    service = TelegramFoundationService(config=_config(), client=client, store=_store(tmp_path))

    await service.initialize()

    assert client.delete_webhook_calls == [True]


@pytest.mark.asyncio
async def test_normal_restart_does_not_drop_pending_updates_again(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_client = FakeTelegramClient()
    first_service = TelegramFoundationService(config=_config(), client=first_client, store=store)

    await first_service.initialize()
    second_client = FakeTelegramClient()
    second_service = TelegramFoundationService(config=_config(), client=second_client, store=store)
    await second_service.initialize()

    assert first_client.delete_webhook_calls == [True]
    assert second_client.delete_webhook_calls == []


@pytest.mark.asyncio
async def test_get_updates_uses_allowed_updates_and_offset(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_activated()
    store.persist_last_update_id(10)
    client = FakeTelegramClient(updates=[[_message(11)]])
    service = TelegramFoundationService(
        config=_config(),
        client=client,
        store=store,
        meshcore_transport=FakeMeshCoreTransport(),
    )

    decisions = await service.poll_once()

    assert decisions[0].reason == "forwarded"
    assert client.get_updates_calls == [
        {"offset": 11, "timeout": 50, "allowed_updates": ("message",)}
    ]
    assert store.last_update_id() == 11


@pytest.mark.asyncio
async def test_telegram_ping_command_sends_pong(tmp_path: Path) -> None:
    service, client, connection = _command_service(tmp_path)

    decision = await service.process_update(_message(100, text="!ping"))

    assert decision.reason == "command"
    assert client.send_message_calls == [{"chat_id": "1001", "text": "pong"}]
    assert _legacy_commands(connection) == [("ping", "succeeded")]


@pytest.mark.asyncio
async def test_telegram_help_command_uses_command_router(tmp_path: Path) -> None:
    service, client, _connection = _command_service(tmp_path)

    await service.process_update(_message(101, text="!help"))

    assert client.send_message_calls
    response = client.send_message_calls[0]["text"]
    assert "!ping" in response
    assert "!estado" in response
    assert "!exterior" in response


@pytest.mark.asyncio
async def test_telegram_estado_ha_command(tmp_path: Path) -> None:
    service, client, _connection = _command_service(tmp_path, ha=FakeHA())

    await service.process_update(_message(102, text="!estado ha private ignored"))

    assert client.send_message_calls == [
        {"chat_id": "1001", "text": "HA: OK\nVersion: 2026.8.0\nName: Home"}
    ]


@pytest.mark.asyncio
async def test_telegram_estado_command(tmp_path: Path) -> None:
    service, client, _connection = _command_service(tmp_path, ha=FakeHA())

    await service.process_update(_message(103, text="!estado"))

    response = client.send_message_calls[0]["text"]
    assert response.startswith("CASA\nHA: OK")
    assert "Companion: TELEGRAM" in response


@pytest.mark.asyncio
async def test_telegram_exterior_configured(tmp_path: Path) -> None:
    service, client, _connection = _command_service(
        tmp_path,
        ha=FakeHA(
            states={
                "sensor.test_temperature": _state("24.6", "°C"),
                "sensor.test_humidity": _state("61", "%"),
            }
        ),
        weather_status=WeatherStatusConfig(
            temperature_entity="sensor.test_temperature",
            humidity_entity="sensor.test_humidity",
            label="Exterior",
        ),
    )

    await service.process_update(_message(104, text="!exterior"))

    assert client.send_message_calls == [
        {"chat_id": "1001", "text": "Exterior: 24.6 °C · Humedad: 61 %"}
    ]


@pytest.mark.asyncio
async def test_telegram_exterior_not_configured(tmp_path: Path) -> None:
    service, client, _connection = _command_service(tmp_path)

    await service.process_update(_message(105, text="!exterior"))

    assert client.send_message_calls == [{"chat_id": "1001", "text": "Exterior: no configurado"}]


@pytest.mark.asyncio
async def test_telegram_unknown_command_responds_in_telegram(tmp_path: Path) -> None:
    service, client, connection = _command_service(tmp_path)

    await service.process_update(_message(106, text="!secret arbitrary private text"))

    assert client.send_message_calls == [
        {"chat_id": "1001", "text": "Comando desconocido. Usa !help"}
    ]
    assert _legacy_commands(connection) == [("unknown", "unknown")]


@pytest.mark.asyncio
async def test_telegram_normal_text_forwards_to_meshcore(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    meshcore = FakeMeshCoreTransport()
    service, client, connection = _command_service(tmp_path, meshcore_transport=meshcore)
    caplog.set_level(logging.INFO, logger="meshcore_control.telegram.service")

    decision = await service.process_update(_message(107, text="hello"))

    assert decision.reason == "forwarded"
    assert client.send_message_calls == []
    assert len(meshcore.sent) == 1
    assert meshcore.sent[0].channel_index == 1
    assert meshcore.sent[0].text == "TG: hello"
    assert connection.execute("SELECT status FROM telegram_bridge_pending").fetchone()[0] == (
        "accepted_by_meshcore_transport"
    )
    assert "Telegram forward confirmation skipped reason=disabled" in caplog.text


@pytest.mark.asyncio
async def test_telegram_normal_text_confirms_when_enabled(tmp_path: Path) -> None:
    meshcore = FakeMeshCoreTransport()
    service, client, _connection = _command_service(
        tmp_path,
        meshcore_transport=meshcore,
        config=_config(send_forward_confirmation=True),
    )

    decision = await service.process_update(_message(117, text="hello"))

    assert decision.reason == "forwarded"
    assert [message.text for message in meshcore.sent] == ["TG: hello"]
    assert client.send_message_calls == [{"chat_id": "1001", "text": "Enviado a MeshCore."}]


@pytest.mark.asyncio
async def test_telegram_forward_pending_record_lock_does_not_crash_after_send(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "audit.db"
    locker = connect_database(str(database_path))

    class LockingMeshCoreTransport(FakeMeshCoreTransport):
        async def send(self, message: OutboundMessage) -> None:
            await super().send(message)
            locker.execute("BEGIN IMMEDIATE")

    meshcore = LockingMeshCoreTransport()
    service, client, connection = _command_service(
        tmp_path,
        client=FakeTelegramClient(),
        meshcore_transport=meshcore,
    )
    connection.execute("PRAGMA busy_timeout=1")

    decision = await service.process_update(_message(70, text="hello"))

    assert decision.reason == "forwarded"
    assert meshcore.sent[0].text == "TG: hello"
    assert client.send_message_calls == []
    assert not connection.in_transaction
    locker.rollback()


@pytest.mark.asyncio
async def test_telegram_forward_applies_prefix_and_channel(tmp_path: Path) -> None:
    meshcore = FakeMeshCoreTransport()
    service, _client, _connection = _command_service(
        tmp_path,
        meshcore_transport=meshcore,
        config=_config(message_prefix="TG: ", meshcore_channel_index=7),
    )

    await service.process_update(_message(118, text="Voy en 10 minutos"))

    assert meshcore.sent[0].channel_index == 7
    assert meshcore.sent[0].text == "TG: Voy en 10 minutos"


@pytest.mark.asyncio
async def test_telegram_forward_can_disable_prefix(tmp_path: Path) -> None:
    meshcore = FakeMeshCoreTransport()
    service, _client, _connection = _command_service(
        tmp_path,
        meshcore_transport=meshcore,
        config=_config(message_prefix=""),
    )

    await service.process_update(_message(119, text="sin prefijo"))

    assert meshcore.sent[0].text == "sin prefijo"


@pytest.mark.asyncio
async def test_telegram_commands_do_not_forward_to_meshcore(tmp_path: Path) -> None:
    meshcore = FakeMeshCoreTransport()
    service, client, _connection = _command_service(tmp_path, meshcore_transport=meshcore)

    await service.process_update(_message(120, text="!ping"))
    await service.process_update(_message(121, text="!estado"))

    assert client.send_message_calls[0] == {"chat_id": "1001", "text": "pong"}
    assert meshcore.sent == []


@pytest.mark.asyncio
async def test_telegram_forward_disabled_does_not_send(tmp_path: Path) -> None:
    meshcore = FakeMeshCoreTransport()
    service, client, _connection = _command_service(
        tmp_path,
        meshcore_transport=meshcore,
        config=_config(forward_telegram_to_meshcore=False),
    )

    decision = await service.process_update(_message(122, text="hello"))

    assert decision.reason == "forward_disabled"
    assert meshcore.sent == []
    assert client.send_message_calls == []


@pytest.mark.asyncio
async def test_telegram_forward_rate_limit(tmp_path: Path) -> None:
    meshcore = FakeMeshCoreTransport()
    service, client, _connection = _command_service(
        tmp_path,
        meshcore_transport=meshcore,
        config=_config(forwarding_rate_limit=RateLimitConfig(commands=1, window_seconds=60)),
    )

    first = await service.process_update(_message(123, text="one"))
    second = await service.process_update(_message(124, text="two"))

    assert first.reason == "forwarded"
    assert second.reason == "rate_limited"
    assert [message.text for message in meshcore.sent] == ["TG: one"]
    assert client.send_message_calls[-1] == {"chat_id": "1001", "text": "Rate limit."}


@pytest.mark.asyncio
async def test_telegram_forward_transport_error(tmp_path: Path) -> None:
    meshcore = FakeMeshCoreTransport(send_error=TimeoutError("meshcore timeout"))
    service, client, connection = _command_service(tmp_path, meshcore_transport=meshcore)

    decision = await service.process_update(_message(125, text="hello"))

    assert decision.reason == "failed"
    assert client.send_message_calls == [
        {"chat_id": "1001", "text": "No se pudo enviar a MeshCore."}
    ]
    assert connection.execute("SELECT status FROM telegram_bridge_pending").fetchone()[0] == (
        "failed"
    )


@pytest.mark.asyncio
async def test_telegram_forward_confirmation_failure_does_not_crash(
    tmp_path: Path,
) -> None:
    meshcore = FakeMeshCoreTransport()
    client = FakeTelegramClient(send_error=TelegramConflictError("conflict"))
    service, _client, connection = _command_service(
        tmp_path,
        client=client,
        meshcore_transport=meshcore,
        config=_config(send_forward_confirmation=True),
    )

    decision = await service.process_update(_message(126, text="hello"))

    assert decision.reason == "forwarded"
    assert [message.text for message in meshcore.sent] == ["TG: hello"]
    assert connection.execute("SELECT status FROM telegram_bridge_pending").fetchone()[0] == (
        "accepted_by_meshcore_transport"
    )


@pytest.mark.asyncio
async def test_telegram_forward_does_not_hold_sqlite_transaction_during_meshcore_send(
    tmp_path: Path,
) -> None:
    connection = connect_database(str(tmp_path / "audit.db"))

    class InspectingMeshCoreTransport(FakeMeshCoreTransport):
        async def send(self, message: OutboundMessage) -> None:
            assert not connection.in_transaction
            await super().send(message)

    registry = build_registry()
    audit_flow = AuditFlow(
        connection=connection,
        legacy=AuditRepository(connection),
        normalized=NormalizedAuditRepository(
            connection,
            NormalizedAuditSettings(
                enabled=True,
                audit_key=AuditKey(key=b"t" * AUDIT_KEY_MIN_BYTES, key_id="telegram-key"),
            ),
        ),
    )
    service = TelegramFoundationService(
        config=_config(),
        client=FakeTelegramClient(),
        store=TelegramStore(connection, audit_key=AuditKey(b"a" * 32)),
        router=CommandRouter(
            registry=registry,
            authorizer=Authorizer(
                {
                    TELEGRAM_SENDER_ID: AuthorizedUser(
                        TELEGRAM_SENDER_ID,
                        "telegram-authorized-user",
                        Role.readonly,
                    )
                },
                room_policies={
                    TELEGRAM_ROOM_ID: RoomPolicy(
                        room_id=TELEGRAM_ROOM_ID,
                        enabled=True,
                        minimum_role=Role.readonly,
                        allow_commands=True,
                    )
                },
            ),
            audit=AuditRepository(connection),
            audit_flow=audit_flow,
            services={"registry": registry, "config": AppConfig()},
            prefix="!",
        ),
        audit_flow=audit_flow,
        meshcore_transport=InspectingMeshCoreTransport(),
        normalized_audit=audit_flow.normalized,
    )

    await service.process_update(_message(127, text="hello"))


@pytest.mark.asyncio
async def test_telegram_forward_records_normalized_bridge_events(tmp_path: Path) -> None:
    meshcore = FakeMeshCoreTransport()
    service, _client, connection = _command_service(tmp_path, meshcore_transport=meshcore)

    await service.process_update(_message(128, text="private forwarded text"))

    rows = connection.execute(
        "SELECT event_id, event_type, correlation_id, causation_event_id, metadata_json "
        "FROM normalized_audit_events ORDER BY id"
    ).fetchall()
    bridge_rows = [row for row in rows if row["event_type"].startswith("bridge.message.")]
    assert [row["event_type"] for row in bridge_rows] == [
        "bridge.message.received",
        "bridge.message.forwarded",
    ]
    assert bridge_rows[0]["causation_event_id"] is None
    assert bridge_rows[1]["causation_event_id"] == bridge_rows[0]["event_id"]
    assert {row["correlation_id"] for row in bridge_rows} == {
        bridge_rows[0]["correlation_id"]
    }
    metadata = json.loads(bridge_rows[1]["metadata_json"])
    assert metadata["result"] == "accepted_by_meshcore_transport"
    assert metadata["direction"] == "telegram_to_meshcore"
    database_text = (tmp_path / "audit.db").read_bytes().decode("utf-8", errors="ignore")
    for forbidden in ("private forwarded text", "1001", "2002", "3003", VALID_TOKEN):
        assert forbidden not in database_text


@pytest.mark.asyncio
async def test_telegram_forward_persists_pending_record_without_raw_content(
    tmp_path: Path,
) -> None:
    meshcore = FakeMeshCoreTransport()
    service, _client, connection = _command_service(tmp_path, meshcore_transport=meshcore)

    await service.process_update(_message(129, text="private content"))

    row = connection.execute(
        "SELECT bridge_message_id, destination_transport, destination_room_id, "
        "content_ref_hash, size_bytes, status FROM telegram_bridge_pending"
    ).fetchone()
    assert row["bridge_message_id"].startswith("bridge:")
    assert row["destination_transport"] == "homeassistant-meshcore"
    assert row["destination_room_id"] == "homeassistant-meshcore:channel:1"
    assert row["content_ref_hash"].startswith("hmac-sha256:v1:")
    assert row["size_bytes"] == len(b"TG: private content")
    assert row["status"] == "accepted_by_meshcore_transport"
    database_text = (tmp_path / "audit.db").read_bytes().decode("utf-8", errors="ignore")
    assert "private content" not in database_text


def test_render_meshcore_forward_message_truncates_utf8_on_boundary() -> None:
    result = render_meshcore_forward_message(text="áéíóú café largo", prefix="TG: ", max_bytes=16)

    assert result.text is not None
    assert result.truncated is True
    assert len(result.text.encode("utf-8")) <= 16
    result.text.encode("utf-8").decode("utf-8")
    assert result.text.endswith("...")


def test_render_meshcore_forward_message_empty_after_strip() -> None:
    assert render_meshcore_forward_message(text="   ", prefix="TG: ", max_bytes=10).text is None


def test_render_meshcore_forward_message_drops_empty_after_truncation() -> None:
    assert render_meshcore_forward_message(text="abc", prefix="", max_bytes=2).text is None


@pytest.mark.asyncio
async def test_telegram_unauthorized_sources_do_not_get_responses(tmp_path: Path) -> None:
    for update in (
        _message(108, chat_id=9999, text="!ping"),
        _message(109, user_id=9999, text="!ping"),
        _message(110, chat_type="group", text="!ping"),
        _message(111, is_bot=True, text="!ping"),
        _message(112, text=None, media=True),
    ):
        service, client, _connection = _command_service(tmp_path / str(update["update_id"]))
        await service.process_update(update)
        assert client.send_message_calls == []


@pytest.mark.asyncio
async def test_telegram_readonly_authorization_denied_without_user(tmp_path: Path) -> None:
    service, client, connection = _command_service(tmp_path, authorized=False)

    await service.process_update(_message(113, text="!ping"))

    assert client.send_message_calls == [{"chat_id": "1001", "text": "No autorizado."}]
    assert _legacy_commands(connection) == [("ping", "unauthorized")]


@pytest.mark.asyncio
async def test_telegram_audit_chain_and_privacy(tmp_path: Path) -> None:
    service, client, connection = _command_service(tmp_path)

    await service.process_update(_message(114, text="!ping private payload"))

    assert client.send_message_calls == [{"chat_id": "1001", "text": "pong"}]
    rows = connection.execute(
        "SELECT event_id, event_type, correlation_id, causation_event_id, transport, "
        "source_room_id, "
        "sender_ref_hash, message_ref_hash, command_name FROM normalized_audit_events ORDER BY id"
    ).fetchall()
    assert [row["event_type"] for row in rows] == [
        "message.received",
        "command.parsed",
        "command.authorization",
        "command.execution",
        "response.sent",
    ]
    assert {row["correlation_id"] for row in rows} == {rows[0]["correlation_id"]}
    assert rows[0]["causation_event_id"] is None
    assert rows[1]["causation_event_id"] == rows[0]["event_id"]
    assert {row["transport"] for row in rows} == {"telegram"}
    assert {row["source_room_id"] for row in rows} == {TELEGRAM_ROOM_ID}
    database_text = (tmp_path / "audit.db").read_bytes().decode("utf-8", errors="ignore")
    for forbidden in ("1001", "2002", "private payload", "3003", VALID_TOKEN):
        assert forbidden not in database_text


@pytest.mark.asyncio
async def test_telegram_send_message_failures_are_audited(tmp_path: Path) -> None:
    client = FakeTelegramClient(send_error=TimeoutError("private timeout"))
    service, _client, connection = _command_service(tmp_path, client=client)

    await service.process_update(_message(115, text="!ping"))

    assert client.send_message_calls == []
    rows = connection.execute(
        "SELECT event_type FROM normalized_audit_events ORDER BY id"
    ).fetchall()
    assert rows[-1]["event_type"] == "response.failed"


@pytest.mark.asyncio
async def test_telegram_send_message_rate_limit_uses_bounded_sleep(tmp_path: Path) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = FakeTelegramClient(send_error=TelegramRateLimitError(30))
    service, _client, _connection = _command_service(tmp_path, client=client)
    service.sleep = fake_sleep
    service.backoff_max_seconds = 2

    await service.process_update(_message(116, text="!ping"))

    assert sleeps == [2]


@pytest.mark.asyncio
async def test_telegram_send_message_conflict_fails_closed(tmp_path: Path) -> None:
    client = FakeTelegramClient(send_error=TelegramConflictError("conflict"))
    service, _client, _connection = _command_service(tmp_path, client=client)

    with pytest.raises(TelegramConflictError):
        await service.process_update(_message(117, text="!ping"))


def _legacy_commands(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = connection.execute(
        "SELECT command, result FROM command_executions ORDER BY id"
    ).fetchall()
    return [(row["command"], row["result"]) for row in rows]


@pytest.mark.asyncio
async def test_duplicate_update_is_ignored_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "audit.db"
    first_store = TelegramStore(connect_database(str(database_path)), audit_key=AuditKey(b"a" * 32))
    service = TelegramFoundationService(
        config=_config(),
        client=FakeTelegramClient(),
        store=first_store,
        meshcore_transport=FakeMeshCoreTransport(),
    )

    first = await service.process_update(_message(20))
    restarted_store = TelegramStore(
        connect_database(str(database_path)),
        audit_key=AuditKey(b"a" * 32),
    )
    restarted_service = TelegramFoundationService(
        config=_config(),
        client=FakeTelegramClient(),
        store=restarted_store,
        meshcore_transport=FakeMeshCoreTransport(),
    )
    duplicate = await restarted_service.process_update(_message(20))

    assert first.reason == "forwarded"
    assert duplicate.reason == "duplicate"


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        (_message(30, chat_id=9999), "chat_not_authorized"),
        (_message(31, user_id=9999), "user_not_authorized"),
        (_message(32, chat_type="group"), "group_ignored"),
        (_message(33, chat_type="supergroup"), "supergroup_ignored"),
        (_message(34, chat_type="channel"), "channel_ignored"),
        (_message(35, is_bot=True), "bot_message"),
        (_message(36, text="", is_bot=False), "empty_text"),
        (_message(37, text=None, media=True), "multimedia_ignored"),
        ({"update_id": 38, "edited_message": {"text": "edited"}}, "edited_message"),
    ],
)
@pytest.mark.asyncio
async def test_ignored_update_types(
    tmp_path: Path,
    update: dict[str, Any],
    reason: str,
) -> None:
    service = TelegramFoundationService(
        config=_config(),
        client=FakeTelegramClient(),
        store=_store(tmp_path),
    )

    decision = await service.process_update(update)

    assert decision.reason == reason


@pytest.mark.asyncio
async def test_rate_limit_uses_bounded_backoff(tmp_path: Path) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        service.stop()

    client = FakeTelegramClient(rate_limited_once=True)
    service = TelegramFoundationService(
        config=_config(),
        client=client,
        store=_store(tmp_path),
        backoff_max_seconds=1,
        sleep=fake_sleep,
    )
    service.store.mark_activated()

    await service.run()

    assert sleeps == [1]


@pytest.mark.asyncio
async def test_shutdown_stops_before_new_poll(tmp_path: Path) -> None:
    client = FakeTelegramClient()
    service = TelegramFoundationService(config=_config(), client=client, store=_store(tmp_path))
    service.stop()

    await service.run()

    assert client.get_updates_calls == []


@pytest.mark.asyncio
async def test_no_sqlite_transaction_open_during_poll_await(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_activated()

    class InspectingClient(FakeTelegramClient):
        async def get_updates(
            self,
            *,
            offset: int | None,
            timeout: int,
            allowed_updates: tuple[str, ...] = ("message",),
        ) -> list[dict[str, Any]]:
            assert not store.connection.in_transaction
            return await super().get_updates(
                offset=offset,
                timeout=timeout,
                allowed_updates=allowed_updates,
            )

    service = TelegramFoundationService(
        config=_config(),
        client=InspectingClient(updates=[[_message(45)]]),
        store=store,
    )

    await service.poll_once()

    assert not store.connection.in_transaction


@pytest.mark.asyncio
async def test_sqlite_and_logs_do_not_store_private_values(tmp_path: Path, caplog) -> None:
    token_file = tmp_path / "telegram.bot_token"
    load_or_import_token(token_import=VALID_TOKEN, token_file=str(token_file))
    store = _store(tmp_path)
    service = TelegramFoundationService(
        config=_config(allowed_private_chat_id="1001", allowed_user_id="2002"),
        client=FakeTelegramClient(),
        store=store,
    )

    with caplog.at_level("INFO"):
        await service.process_update(_message(50, text="sensitive body"))

    database_text = (tmp_path / "audit.db").read_bytes().decode("utf-8", errors="ignore")
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    for forbidden in (VALID_TOKEN, "1001", "2002", "sensitive body", "3003"):
        assert forbidden not in database_text
        assert forbidden not in log_text


@pytest.mark.asyncio
async def test_audit_uses_hmac_references(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = TelegramFoundationService(
        config=_config(),
        client=FakeTelegramClient(),
        store=store,
    )

    await service.process_update(_message(60))

    row = store.connection.execute(
        "SELECT update_ref_hash, chat_ref_hash, user_ref_hash FROM telegram_audit_events"
    ).fetchone()
    assert row["update_ref_hash"].startswith("hmac-sha256:v1:")
    assert row["chat_ref_hash"].startswith("hmac-sha256:v1:")
    assert row["user_ref_hash"].startswith("hmac-sha256:v1:")


def test_options_json_with_telegram_is_not_token_storage(tmp_path: Path) -> None:
    options = {
        "channel_index": 1,
        "allow_unidentified_readonly_testing": True,
        "telegram": {
            "enabled": False,
            "bot_token_import": "",
            "bot_token_file": "/data/telegram.bot_token",
            "allowed_private_chat_id": "",
            "allowed_user_id": "",
            "meshcore_channel_index": 1,
            "forward_meshcore_to_telegram": True,
            "forward_telegram_to_meshcore": True,
            "command_prefix": "!",
            "max_meshcore_message_length": 180,
            "message_prefix": "",
        },
    }
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options), encoding="utf-8")

    assert VALID_TOKEN not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_bot_api_client_uses_expected_long_polling_payload() -> None:
    requests: list[dict[str, Any]] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        headers = request.decode("ascii")
        content_length = 0
        for line in headers.splitlines():
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
        body = await reader.readexactly(content_length)
        first_line = headers.splitlines()[0]
        requests.append(
            {
                "path": first_line.split()[1],
                "body": json.loads(body.decode("utf-8")),
            }
        )
        response_body = json.dumps({"ok": True, "result": []}).encode("utf-8")
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Length: {len(response_body)}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n\r\n"
            + response_body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        client = TelegramBotApiClient(
            token=TelegramToken(VALID_TOKEN),
            base_url=f"http://127.0.0.1:{port}",
        )

        await client.delete_webhook(drop_pending_updates=True)
        await client.get_updates(offset=12, timeout=50)
        await client.send_message(chat_id="1001", text="pong")
    finally:
        server.close()
        await server.wait_closed()

    assert requests == [
        {
            "path": f"/bot{VALID_TOKEN}/deleteWebhook",
            "body": {"drop_pending_updates": True},
        },
        {
            "path": f"/bot{VALID_TOKEN}/getUpdates",
            "body": {"timeout": 50, "allowed_updates": ["message"], "offset": 12},
        },
        {
            "path": f"/bot{VALID_TOKEN}/sendMessage",
            "body": {
                "chat_id": "1001",
                "text": "pong",
                "disable_web_page_preview": True,
            },
        },
    ]


@pytest.mark.asyncio
async def test_bridge_health_tracks_telegram_to_meshcore_forward_success(tmp_path: Path) -> None:
    health = BridgeHealthState()
    meshcore = FakeMeshCoreTransport()
    service, client, _connection = _command_service(
        tmp_path,
        meshcore_transport=meshcore,
        health=health,
    )

    decision = await service.process_update(_message(70, text="texto normal"))

    assert decision.reason == "forwarded"
    assert [item.text for item in meshcore.sent] == ["TG: texto normal"]
    assert client.send_message_calls == []
    snapshot = health.snapshot()
    assert snapshot.tg_to_mc_success == 1
    assert snapshot.tg_to_mc_failed == 0


@pytest.mark.asyncio
async def test_bridge_health_tracks_telegram_to_meshcore_forward_failure(tmp_path: Path) -> None:
    health = BridgeHealthState()
    meshcore = FakeMeshCoreTransport(send_error=TimeoutError())
    service, client, _connection = _command_service(
        tmp_path,
        meshcore_transport=meshcore,
        health=health,
    )

    decision = await service.process_update(_message(71, text="texto normal"))

    assert decision.reason == "failed"
    assert client.send_message_calls == [
        {"chat_id": "1001", "text": "No se pudo enviar a MeshCore."}
    ]
    snapshot = health.snapshot()
    assert snapshot.tg_to_mc_success == 0
    assert snapshot.tg_to_mc_failed == 1
    assert snapshot.last_failure_reason == "transport_error"


@pytest.mark.asyncio
async def test_bridge_command_works_from_telegram_without_forwarding(tmp_path: Path) -> None:
    health = BridgeHealthState()
    health.configure(
        telegram_enabled=True,
        forward_telegram_to_meshcore=True,
        forward_meshcore_to_telegram=True,
        forward_confirmation_enabled=False,
    )
    meshcore = FakeMeshCoreTransport()
    service, client, _connection = _command_service(
        tmp_path,
        meshcore_transport=meshcore,
        health=health,
    )

    decision = await service.process_update(_message(72, text="!bridge"))

    assert decision.reason == "command"
    assert len(client.send_message_calls) == 1
    assert "Version: 0.1.16" in client.send_message_calls[0]["text"]
    assert meshcore.sent == []
