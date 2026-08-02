from __future__ import annotations

import asyncio
import sqlite3

from meshcore_control.app import BridgeService
from meshcore_control.auth.authorization import AuthorizedUser, Authorizer, RoomPolicy
from meshcore_control.auth.roles import Role
from meshcore_control.commands.registry import CommandContext, CommandDefinition
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import AppConfig
from meshcore_control.models import InboundMessage, RoomRef
from meshcore_control.plugins import build_registry
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.database import connect_database
from meshcore_control.storage.repositories import AuditRepository
from meshcore_control.transport.fake import FakeTransport

ROOM_ID = "homeassistant-meshcore:channel:1"
OTHER_ROOM_ID = "homeassistant-meshcore:channel:2"
SENDER_ID = "meshcore-pubkey-prefix:abcdef123456"


def build_service(
    connection: sqlite3.Connection,
    *,
    users: dict[str, AuthorizedUser],
    room_policies: dict[str, RoomPolicy],
) -> tuple[BridgeService, FakeTransport, AuditRepository]:
    registry = build_registry()
    registry.register(
        CommandDefinition(
            name="home-test",
            aliases=(),
            group="test",
            usage="!home-test",
            help_text="Synthetic home-role command for authorization tests.",
            minimum_role=Role.home,
            confirmation_required=False,
            handler=_home_test,
        )
    )
    audit = AuditRepository(connection)
    router = CommandRouter(
        registry=registry,
        authorizer=Authorizer(users, room_policies=room_policies),
        audit=audit,
        services={"registry": registry, "config": AppConfig()},
        prefix="!",
    )
    transport = FakeTransport()
    return (
        BridgeService(
            transport=transport,
            router=router,
            deduplicator=Deduplicator(connection, window_seconds=300),
            rate_limiter=RateLimiter(max_commands=10, window_seconds=60),
            channel_index=1,
        ),
        transport,
        audit,
    )


async def _home_test(_context: CommandContext, _args: list[str]) -> str:
    return "home ok"


def inbound(
    text: str,
    *,
    sender_id: str = SENDER_ID,
    room_id: str = ROOM_ID,
    channel_index: int = 1,
    message_id: str = "msg-1",
    sender_name: str | None = None,
) -> InboundMessage:
    room = RoomRef(
        transport="homeassistant-meshcore",
        room_id=room_id,
        room_kind="meshcore_channel",
        metadata={"channel_index": channel_index},
    )
    metadata = {}
    if sender_name:
        metadata["sender_name"] = sender_name
    return InboundMessage(
        transport="homeassistant-meshcore",
        message_id=message_id,
        sender_id=sender_id,
        channel_index=channel_index,
        text=text,
        metadata=metadata,
        source_room=room,
        reply_target=room,
    )


def readonly_room(*, enabled: bool = True, room_id: str = ROOM_ID) -> RoomPolicy:
    return RoomPolicy(
        room_id=room_id,
        enabled=enabled,
        minimum_role=Role.readonly,
        allow_commands=True,
    )


def test_authorized_pubkey_in_configured_room_gets_ping(tmp_path) -> None:
    service, transport, audit = build_service(
        connect_database(str(tmp_path / "audit.db")),
        users={SENDER_ID: AuthorizedUser(SENDER_ID, "admin-device", Role.admin)},
        room_policies={ROOM_ID: readonly_room()},
    )

    outbound = asyncio.run(service.process_message(inbound("!ping")))

    assert outbound is not None
    assert outbound.text == "pong"
    assert transport.sent[-1].text == "pong"
    assert audit.count_commands() == 1


def test_unregistered_pubkey_in_configured_room_is_unauthorized(tmp_path) -> None:
    service, transport, audit = build_service(
        connect_database(str(tmp_path / "audit.db")),
        users={},
        room_policies={ROOM_ID: readonly_room()},
    )

    outbound = asyncio.run(service.process_message(inbound("!ping")))

    assert outbound is not None
    assert outbound.text == "No autorizado."
    assert transport.sent[-1].text == "No autorizado."
    assert audit.count_commands() == 1


def test_authorized_pubkey_in_wrong_room_is_ignored(tmp_path) -> None:
    service, transport, audit = build_service(
        connect_database(str(tmp_path / "audit.db")),
        users={SENDER_ID: AuthorizedUser(SENDER_ID, "admin-device", Role.admin)},
        room_policies={ROOM_ID: readonly_room()},
    )

    outbound = asyncio.run(
        service.process_message(
            inbound("!ping", room_id=OTHER_ROOM_ID, channel_index=1)
        )
    )

    assert outbound is None
    assert transport.sent == []
    assert audit.count_commands() == 0


def test_room_disabled_is_ignored_before_command_execution(tmp_path) -> None:
    service, transport, audit = build_service(
        connect_database(str(tmp_path / "audit.db")),
        users={SENDER_ID: AuthorizedUser(SENDER_ID, "admin-device", Role.admin)},
        room_policies={ROOM_ID: readonly_room(enabled=False)},
    )

    outbound = asyncio.run(service.process_message(inbound("!ping")))

    assert outbound is None
    assert transport.sent == []
    assert audit.count_commands() == 0


def test_sender_role_room_policy_and_command_role_precedence(tmp_path) -> None:
    service, transport, _audit = build_service(
        connect_database(str(tmp_path / "audit.db")),
        users={SENDER_ID: AuthorizedUser(SENDER_ID, "admin-device", Role.home)},
        room_policies={
            ROOM_ID: RoomPolicy(
                room_id=ROOM_ID,
                enabled=True,
                minimum_role=Role.operator,
                allow_commands=True,
            )
        },
    )

    denied_by_room_policy = asyncio.run(service.process_message(inbound("!ping")))

    assert denied_by_room_policy is not None
    assert denied_by_room_policy.text == "No autorizado."
    assert transport.sent[-1].text == "No autorizado."

    service, transport, _audit = build_service(
        connect_database(str(tmp_path / "audit-2.db")),
        users={SENDER_ID: AuthorizedUser(SENDER_ID, "admin-device", Role.readonly)},
        room_policies={ROOM_ID: readonly_room()},
    )

    denied_by_command_role = asyncio.run(
        service.process_message(inbound("!home-test", message_id="msg-2"))
    )

    assert denied_by_command_role is not None
    assert denied_by_command_role.text == "No autorizado."
    assert transport.sent[-1].text == "No autorizado."


def test_sender_name_never_authorizes(tmp_path) -> None:
    service, transport, _audit = build_service(
        connect_database(str(tmp_path / "audit.db")),
        users={SENDER_ID: AuthorizedUser(SENDER_ID, "admin-device", Role.admin)},
        room_policies={ROOM_ID: readonly_room()},
    )

    outbound = asyncio.run(
        service.process_message(
            inbound(
                "!ping",
                sender_id="meshcore-pubkey-prefix:not-authorized",
                sender_name="admin-device",
            )
        )
    )

    assert outbound is not None
    assert outbound.text == "No autorizado."
    assert transport.sent[-1].text == "No autorizado."


def test_unidentified_synthetic_sender_is_readonly_in_configured_room(tmp_path) -> None:
    sender_id = "test:unidentified:channel:1"
    service, transport, _audit = build_service(
        connect_database(str(tmp_path / "audit.db")),
        users={sender_id: AuthorizedUser(sender_id, "unidentified-channel-testing", Role.readonly)},
        room_policies={ROOM_ID: readonly_room()},
    )

    allowed = asyncio.run(
        service.process_message(inbound("!ping", sender_id=sender_id, message_id="msg-1"))
    )
    denied_write = asyncio.run(
        service.process_message(
            inbound("!home-test", sender_id=sender_id, message_id="msg-2")
        )
    )
    ignored_wrong_room = asyncio.run(
        service.process_message(
            inbound(
                "!ping",
                sender_id=sender_id,
                room_id=OTHER_ROOM_ID,
                channel_index=1,
                message_id="msg-3",
            )
        )
    )

    assert allowed is not None
    assert allowed.text == "pong"
    assert denied_write is not None
    assert denied_write.text == "No autorizado."
    assert ignored_wrong_room is None
    assert [item.text for item in transport.sent] == ["pong", "No autorizado."]
