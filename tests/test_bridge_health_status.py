from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path

from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer, RoomPolicy
from meshcore_control.auth.roles import Role
from meshcore_control.bridge_health import BridgeHealthState
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig
from meshcore_control.homeassistant_app_health import main as healthcheck_main
from meshcore_control.models import InboundMessage, MessageIdentity, RoomRef, SenderIdentity
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.telegram.identity import TELEGRAM_ROOM_ID, TELEGRAM_SENDER_ID, telegram_room
from meshcore_control.transport.fake import FakeTransport


def _service(
    tmp_path: Path,
    *,
    health: BridgeHealthState,
    transport_name: str = "homeassistant-meshcore",
    channel_index: int = 1,
) -> tuple[BridgeService, FakeTransport]:
    connection = connect_database(str(tmp_path / "audit.db"))
    registry = build_registry()
    sender_id = (
        TELEGRAM_SENDER_ID
        if transport_name == "telegram"
        else "meshcore-pubkey-prefix:abc123"
    )
    room_id = TELEGRAM_ROOM_ID if transport_name == "telegram" else f"{transport_name}:channel:1"
    audit = AuditRepository(connection)
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(
            {sender_id: AuthorizedUser(sender_id, "tester", Role.readonly)},
            room_policies={
                room_id: RoomPolicy(
                    room_id=room_id,
                    enabled=True,
                    minimum_role=Role.readonly,
                    allow_commands=True,
                )
            },
        ),
        audit=audit,
        services={"registry": registry, "config": AppConfig(), "bridge_health": health},
        prefix="!",
    )
    transport = FakeTransport()
    return (
        BridgeService(
            transport=transport,
            router=router,
            deduplicator=Deduplicator(connection, window_seconds=300),
            rate_limiter=RateLimiter(max_commands=10, window_seconds=60),
            channel_index=channel_index,
        ),
        transport,
    )


def _message(
    *,
    transport: str = "homeassistant-meshcore",
    text: str = "!bridge",
    channel_index: int = 1,
) -> InboundMessage:
    sender_id = TELEGRAM_SENDER_ID if transport == "telegram" else "meshcore-pubkey-prefix:abc123"
    room = telegram_room() if transport == "telegram" else RoomRef.channel(
        transport=transport,
        channel_index=channel_index,
    )
    message = MessageIdentity.from_message_id(
        transport=transport,
        room_id=room.room_id,
        message_id=f"{transport}:message-1",
    )
    return InboundMessage(
        transport=transport,
        message_id=message.message_id,
        sender_id=sender_id,
        channel_index=channel_index,
        text=text,
        source_room=room,
        reply_target=room,
        sender=SenderIdentity.from_sender_id(sender_id=sender_id, transport_scope=transport),
        message=message,
    )


def _configured_health(tmp_path: Path) -> BridgeHealthState:
    health = BridgeHealthState(healthcheck_path=str(tmp_path / "health.json"))
    health.configure(
        telegram_enabled=True,
        forward_telegram_to_meshcore=True,
        forward_meshcore_to_telegram=True,
        forward_confirmation_enabled=False,
    )
    health.set_meshcore_connected(True)
    health.set_telegram_polling("connected")
    return health


def test_bridge_command_from_meshcore_uses_compact_lora_output(tmp_path: Path) -> None:
    health = _configured_health(tmp_path)
    service, transport = _service(tmp_path, health=health)

    outbound = asyncio.run(service.process_message(_message()))

    assert outbound is not None
    assert "Bridge 0.1.16" in outbound.text
    assert "MC:on" in outbound.text
    assert "TG:on" in outbound.text
    assert "T2M:on" in outbound.text
    assert "M2T:on" in outbound.text
    assert "CF:off" in outbound.text
    assert len(outbound.text) < 180
    assert transport.sent[-1].reply_target == outbound.reply_target


def test_bridge_command_from_telegram_returns_full_status_to_telegram(tmp_path: Path) -> None:
    health = _configured_health(tmp_path)
    service, transport = _service(tmp_path, health=health, transport_name="telegram")

    outbound = asyncio.run(service.process_message(_message(transport="telegram")))

    assert outbound is not None
    assert "Version: 0.1.16" in outbound.text
    assert "MeshCore: connected" in outbound.text
    assert "Telegram: connected" in outbound.text
    assert "Channel: 1" in outbound.text
    assert "TG confirm: off" in outbound.text
    assert transport.sent[-1].reply_target == _message(transport="telegram").reply_target


def test_bridge_status_reports_disabled_and_degraded_states(tmp_path: Path) -> None:
    health = BridgeHealthState(healthcheck_path=str(tmp_path / "health.json"))
    health.configure(
        telegram_enabled=False,
        forward_telegram_to_meshcore=False,
        forward_meshcore_to_telegram=True,
        forward_confirmation_enabled=True,
    )
    health.set_audit_db_health("degraded", reason="database_locked")
    health.set_telegram_db_health("degraded", reason="storage_error")
    service, _transport = _service(tmp_path, health=health)

    outbound = asyncio.run(service.process_message(_message()))

    assert outbound is not None
    assert "TG:off" in outbound.text
    assert "T2M:off" in outbound.text
    assert "M2T:on" in outbound.text
    assert "CF:on" in outbound.text
    assert "DB A:degraded T:degraded" in outbound.text
    assert "Err:storage_error" in outbound.text


def test_bridge_status_reports_telegram_enabled_before_polling(tmp_path: Path) -> None:
    health = BridgeHealthState()
    health.configure(
        telegram_enabled=True,
        forward_telegram_to_meshcore=True,
        forward_meshcore_to_telegram=False,
        forward_confirmation_enabled=False,
    )
    service, _transport = _service(tmp_path, health=health, transport_name="telegram")

    outbound = asyncio.run(service.process_message(_message(transport="telegram")))

    assert outbound is not None
    assert "Telegram: enabled" in outbound.text


def test_bridge_health_counters_are_concurrency_safe(tmp_path: Path) -> None:
    health = _configured_health(tmp_path)

    def worker() -> None:
        for _ in range(100):
            health.record_tg_to_mc(success=True)
            health.record_mc_to_tg(success=False, reason="transport_error")
            health.record_command_processed()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    snapshot = health.snapshot()
    assert snapshot.tg_to_mc_success == 500
    assert snapshot.mc_to_tg_failed == 500
    assert snapshot.commands_processed == 500
    assert snapshot.last_failure_reason == "transport_error"


def test_bridge_status_does_not_expose_sensitive_identifiers(tmp_path: Path) -> None:
    health = _configured_health(tmp_path)
    health.record_failure("sender_id meshcore-pubkey-prefix:abcdef token 123")
    service, _transport = _service(tmp_path, health=health)

    outbound = asyncio.run(service.process_message(_message()))

    assert outbound is not None
    forbidden = [
        "meshcore-pubkey-prefix",
        "abcdef",
        "telegram:user",
        "chat",
        "token",
        "sender_id",
    ]
    for marker in forbidden:
        assert marker not in outbound.text


def test_healthcheck_accepts_degraded_payload(tmp_path: Path, monkeypatch) -> None:
    health_path = tmp_path / "health.json"
    health = BridgeHealthState(healthcheck_path=str(health_path))
    health.configure(
        telegram_enabled=True,
        forward_telegram_to_meshcore=True,
        forward_meshcore_to_telegram=True,
        forward_confirmation_enabled=False,
    )
    health.set_audit_db_health("degraded", reason="database_locked")
    payload = json.loads(health_path.read_text(encoding="utf-8"))

    assert payload["status"] == "degraded"
    monkeypatch.setattr(
        "meshcore_control.homeassistant_app_health.APP_HEALTHCHECK_PATH",
        health_path,
    )

    healthcheck_main()


def test_healthcheck_write_failure_does_not_raise(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    health_path = tmp_path / "secret-data" / "health.json"
    health = BridgeHealthState(healthcheck_path=str(health_path))

    def fail_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        raise OSError("permission denied")

    monkeypatch.setattr("meshcore_control.bridge_health.tempfile.mkstemp", fail_mkstemp)

    with caplog.at_level(logging.WARNING):
        health.record_command_processed()

    assert "Bridge healthcheck write skipped reason=storage_error" in caplog.text
    assert "secret-data" not in caplog.text
    assert not health_path.exists()


def test_healthcheck_temp_file_is_removed_after_replace_failure(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    health_path = tmp_path / "health.json"
    temp_files: list[str] = []
    real_mkstemp = tempfile_mkstemp()

    def tracking_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)
        temp_files.append(name)
        return fd, name

    def fail_replace(source: str, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("meshcore_control.bridge_health.tempfile.mkstemp", tracking_mkstemp)
    monkeypatch.setattr("meshcore_control.bridge_health.os.replace", fail_replace)

    with caplog.at_level(logging.WARNING):
        health = BridgeHealthState(healthcheck_path=str(health_path))
        health.record_command_processed()

    assert temp_files
    assert all(not os.path.exists(name) for name in temp_files)
    assert "Bridge healthcheck write skipped reason=storage_error" in caplog.text
    assert str(tmp_path) not in caplog.text


def tempfile_mkstemp():
    import tempfile

    return tempfile.mkstemp
