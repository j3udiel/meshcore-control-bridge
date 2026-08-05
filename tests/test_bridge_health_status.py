from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer, RoomPolicy
from meshcore_control.auth.roles import Role
from meshcore_control.bridge_health import (
    BridgeHealthSnapshot,
    BridgeHealthState,
    relative_time,
    render_last_activity,
)
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


def _last_activity_health(tmp_path: Path) -> BridgeHealthState:
    now = datetime.now(UTC)
    health = BridgeHealthState(
        healthcheck_path=str(tmp_path / "health.json"),
        started_at=now - timedelta(hours=4, minutes=18),
    )
    health.configure(
        telegram_enabled=True,
        forward_telegram_to_meshcore=True,
        forward_meshcore_to_telegram=True,
        forward_confirmation_enabled=False,
    )
    health.set_meshcore_connected(True)
    health.set_telegram_polling("connected")
    with health._lock:
        health._last_tg_to_mc = now - timedelta(minutes=2)
        health._last_mc_to_tg = now - timedelta(minutes=8)
        health._tg_to_mc_success = 14
        health._tg_to_mc_failed = 0
        health._mc_to_tg_success = 9
        health._mc_to_tg_failed = 0
        health._commands_processed = 23
        health._last_failure = None
        health._last_failure_reason = "none"
    return health


def test_bridge_command_from_meshcore_uses_compact_lora_output(tmp_path: Path) -> None:
    health = _configured_health(tmp_path)
    service, transport = _service(tmp_path, health=health)

    outbound = asyncio.run(service.process_message(_message()))

    assert outbound is not None
    assert "Bridge 0.1.18" in outbound.text
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
    assert "Version: 0.1.18" in outbound.text
    assert "MeshCore: connected" in outbound.text
    assert "Telegram: connected" in outbound.text
    assert "Channel: 1" in outbound.text
    assert "TG confirm: off" in outbound.text
    assert transport.sent[-1].reply_target == _message(transport="telegram").reply_target


def test_last_command_from_telegram_returns_detailed_activity(tmp_path: Path) -> None:
    health = _last_activity_health(tmp_path)
    service, transport = _service(tmp_path, health=health, transport_name="telegram")

    outbound = asyncio.run(service.process_message(_message(transport="telegram", text="!last")))

    assert outbound is not None
    assert "Last activity" in outbound.text
    assert "TG -> MC: 2m ago" in outbound.text
    assert "MC -> TG: 8m ago" in outbound.text
    assert "TG -> MC: 14 success / 0 failed" in outbound.text
    assert "MC -> TG: 9 success / 0 failed" in outbound.text
    assert "Commands: 23" in outbound.text
    assert "Last error: none" in outbound.text
    assert "Last error time: never" in outbound.text
    assert "Uptime: 4h 18m" in outbound.text
    assert transport.sent[-1].reply_target == _message(transport="telegram").reply_target


def test_last_command_from_meshcore_returns_compact_activity(tmp_path: Path) -> None:
    health = _last_activity_health(tmp_path)
    service, transport = _service(tmp_path, health=health)

    outbound = asyncio.run(service.process_message(_message(text="!last")))

    assert outbound is not None
    assert outbound.text == "\n".join(
        [
            "Last",
            "T2M:2m M2T:8m",
            "OK:14/9 F:0/0",
            "Cmd:23 Up:4h18m",
            "Err:none",
        ]
    )
    assert len(outbound.text) < 180
    assert transport.sent[-1].reply_target == outbound.reply_target


def test_last_command_initial_state_uses_never(tmp_path: Path) -> None:
    health = BridgeHealthState(started_at=datetime.now(UTC))
    service, _transport = _service(tmp_path, health=health, transport_name="telegram")

    outbound = asyncio.run(service.process_message(_message(transport="telegram", text="!last")))

    assert outbound is not None
    assert "TG -> MC: never" in outbound.text
    assert "MC -> TG: never" in outbound.text
    assert "TG -> MC: 0 success / 0 failed" in outbound.text
    assert "MC -> TG: 0 success / 0 failed" in outbound.text
    assert "Commands: 0" in outbound.text
    assert "Uptime: now" in outbound.text


def test_last_command_handles_missing_health_state_safely(tmp_path: Path) -> None:
    service, _transport = _service(tmp_path, health=_configured_health(tmp_path))
    service.router.services.pop("bridge_health")

    outbound = asyncio.run(service.process_message(_message(text="!last")))

    assert outbound is not None
    assert outbound.text == "Last: N/D"


def test_last_command_reports_safe_error_reason(tmp_path: Path) -> None:
    health = _last_activity_health(tmp_path)
    health.record_failure("token chat_id sender_id pubkey")
    service, _transport = _service(tmp_path, health=health)

    outbound = asyncio.run(service.process_message(_message(text="!last")))

    assert outbound is not None
    assert "Err:storage_error" in outbound.text
    for marker in ["token", "chat_id", "sender_id", "pubkey"]:
        assert marker not in outbound.text


def test_last_command_compact_output_caps_large_counter_display(tmp_path: Path) -> None:
    health = _last_activity_health(tmp_path)
    with health._lock:
        health._tg_to_mc_success = 123_456
        health._mc_to_tg_success = 999_999_999
        health._tg_to_mc_failed = 10_001
        health._mc_to_tg_failed = 0
        health._commands_processed = 4_000_000
    service, _transport = _service(tmp_path, health=health)

    outbound = asyncio.run(service.process_message(_message(text="!last")))

    assert outbound is not None
    assert "OK:123k/999k+ F:10k/0" in outbound.text
    assert "Cmd:999k+" in outbound.text
    assert len(outbound.text) < 180


def test_last_command_naive_datetime_returns_safe_response(tmp_path: Path) -> None:
    health = _last_activity_health(tmp_path)
    with health._lock:
        health._last_tg_to_mc = datetime(2026, 8, 6, 12, 0)
    service, _transport = _service(tmp_path, health=health)

    outbound = asyncio.run(service.process_message(_message(text="!last")))

    assert outbound is not None
    assert outbound.text == "Last: N/D"


def test_last_command_help_is_visible_to_readonly_user(tmp_path: Path) -> None:
    health = _configured_health(tmp_path)
    service, _transport = _service(tmp_path, health=health, transport_name="telegram")

    outbound = asyncio.run(service.process_message(_message(transport="telegram", text="!help")))

    assert outbound is not None
    assert "!last" in outbound.text


def test_last_activity_does_not_expose_sensitive_identifiers(tmp_path: Path) -> None:
    health = _last_activity_health(tmp_path)
    service, _transport = _service(tmp_path, health=health, transport_name="telegram")

    outbound = asyncio.run(service.process_message(_message(transport="telegram", text="!last")))

    assert outbound is not None
    forbidden = [
        "meshcore-pubkey-prefix",
        "telegram:user",
        "1001",
        "2002",
        "message-1",
        "corr:",
        "/data",
        "sensor.",
    ]
    for marker in forbidden:
        assert marker not in outbound.text


def test_last_activity_state_resets_with_new_health_state(tmp_path: Path) -> None:
    active = _last_activity_health(tmp_path)
    fresh = BridgeHealthState(started_at=active.started_at)

    active_text = render_last_activity(active.snapshot())
    fresh_text = render_last_activity(fresh.snapshot())

    assert "TG -> MC: 14 success / 0 failed" in active_text
    assert "TG -> MC: 0 success / 0 failed" in fresh_text
    assert "TG -> MC: never" in fresh_text


def test_relative_time_formats_are_deterministic() -> None:
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)

    assert relative_time(None, now=now) == "never"
    assert relative_time(now, now=now) == "now"
    assert relative_time(now - timedelta(seconds=9), now=now) == "9s"
    assert relative_time(now - timedelta(minutes=2), now=now) == "2m"
    assert relative_time(now - timedelta(hours=4, minutes=18), now=now) == "4h18m"
    assert relative_time(now - timedelta(days=3, hours=2), now=now) == "3d2h"


def test_relative_time_future_timestamp_is_now() -> None:
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)

    assert relative_time(now + timedelta(seconds=30), now=now) == "now"


def test_relative_time_rejects_naive_datetime() -> None:
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="timezone-aware"):
        relative_time(datetime(2026, 8, 5, 11, 59), now=now)


def test_last_activity_render_can_use_explicit_snapshot_time() -> None:
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    health = BridgeHealthState(started_at=now - timedelta(hours=1))
    snapshot: BridgeHealthSnapshot = replace(
        health.snapshot(),
        uptime_seconds=3600,
        last_tg_to_mc=now - timedelta(seconds=30),
        last_mc_to_tg=now - timedelta(minutes=1),
        tg_to_mc_success=1,
        mc_to_tg_failed=2,
        commands_processed=3,
        last_failure=now - timedelta(hours=2),
        last_failure_reason="transport_error",
    )

    text = render_last_activity(snapshot, now=now)

    assert "TG -> MC: 30s ago" in text
    assert "MC -> TG: 1m ago" in text
    assert "MC -> TG: 0 success / 2 failed" in text
    assert "Commands: 3" in text
    assert "Last error: transport_error" in text
    assert "Last error time: 2h0m ago" in text
    assert "Uptime: 1h 0m" in text


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
