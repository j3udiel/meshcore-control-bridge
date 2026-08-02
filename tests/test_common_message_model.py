from __future__ import annotations

import pytest

from meshcore_control.models import (
    NORMALIZED_MESSAGE_SCHEMA_VERSION,
    InboundMessage,
    MessageIdentity,
    MessageOrigin,
    OutboundMessage,
    RoomRef,
    SenderIdentity,
)


def test_legacy_inbound_message_gets_normalized_fields() -> None:
    message = InboundMessage(
        transport="fake",
        message_id="msg-1",
        sender_id="sender-1",
        channel_index=1,
        text="!ping",
    )

    assert message.schema_version == NORMALIZED_MESSAGE_SCHEMA_VERSION
    assert message.channel_index == 1
    assert message.source_room is not None
    assert message.source_room.transport == "fake"
    assert message.source_room.room_id == "fake:channel:1"
    assert message.source_room.metadata["channel_index"] == 1
    assert message.reply_target == message.source_room
    assert message.sender is not None
    assert message.sender.sender_id == "sender-1"
    assert message.sender.transport_scope == "fake"
    assert message.message is not None
    assert message.message.message_id == "msg-1"
    assert message.message.origin.room_id == "fake:channel:1"
    assert message.message.correlation_id.startswith("corr:")


def test_inbound_message_accepts_explicit_source_room_and_reply_target() -> None:
    source_room = RoomRef(
        transport="telegram",
        room_id="telegram:chat:123",
        room_kind="telegram_chat",
    )
    reply_target = RoomRef(
        transport="telegram",
        room_id="telegram:chat:123:thread:456",
        room_kind="telegram_chat",
    )
    sender = SenderIdentity(
        sender_id="telegram-user:primary-bot:789",
        transport_scope="telegram",
        identity_kind="telegram_user_id",
        stable=True,
    )
    identity = MessageIdentity(
        message_id="42",
        id_kind="platform",
        correlation_id="telegram:42",
        origin=MessageOrigin(
            transport="telegram",
            room_id="telegram:chat:123",
            message_id="42",
        ),
    )

    message = InboundMessage(
        transport="telegram",
        message_id="42",
        sender_id="telegram-user:primary-bot:789",
        channel_index=1,
        text="!help",
        source_room=source_room,
        reply_target=reply_target,
        sender=sender,
        message=identity,
    )

    assert message.source_room == source_room
    assert message.reply_target == reply_target
    assert message.sender == sender
    assert message.message == identity


def test_inbound_message_rejects_unsupported_schema_version() -> None:
    with pytest.raises(ValueError, match="unsupported message schema_version"):
        InboundMessage(
            transport="fake",
            message_id="msg-1",
            sender_id="sender-1",
            channel_index=1,
            text="!ping",
            schema_version=999,
        )


def test_inbound_message_rejects_source_room_transport_mismatch() -> None:
    with pytest.raises(ValueError, match="source_room.transport"):
        InboundMessage(
            transport="fake",
            message_id="msg-1",
            sender_id="sender-1",
            channel_index=1,
            text="!ping",
            source_room=RoomRef(
                transport="telegram",
                room_id="telegram:chat:123",
                room_kind="telegram_chat",
            ),
        )


def test_inbound_message_rejects_reply_target_transport_mismatch() -> None:
    with pytest.raises(ValueError, match="reply_target.transport"):
        InboundMessage(
            transport="fake",
            message_id="msg-1",
            sender_id="sender-1",
            channel_index=1,
            text="!ping",
            reply_target=RoomRef(
                transport="telegram",
                room_id="telegram:chat:123",
                room_kind="telegram_chat",
            ),
        )


def test_inbound_message_rejects_sender_transport_scope_mismatch() -> None:
    with pytest.raises(ValueError, match="sender.transport_scope"):
        InboundMessage(
            transport="fake",
            message_id="msg-1",
            sender_id="sender-1",
            channel_index=1,
            text="!ping",
            sender=SenderIdentity(
                sender_id="sender-1",
                transport_scope="telegram",
                identity_kind="telegram_user_id",
                stable=True,
            ),
        )


def test_messages_without_message_id_get_distinct_correlation_ids() -> None:
    first = InboundMessage(
        transport="fake",
        message_id=None,
        sender_id="sender-1",
        channel_index=1,
        text="!ping",
    )
    second = InboundMessage(
        transport="fake",
        message_id=None,
        sender_id="sender-1",
        channel_index=1,
        text="!ping",
    )

    assert first.message is not None
    assert second.message is not None
    assert first.message.message_id is None
    assert second.message.message_id is None
    assert first.message.id_kind == "missing"
    assert second.message.id_kind == "missing"
    assert first.message.correlation_id != second.message.correlation_id


def test_metadata_is_defensively_copied() -> None:
    metadata = {"channel_index": 1}
    room = RoomRef(
        transport="fake",
        room_id="fake:channel:1",
        room_kind="meshcore_channel",
        metadata=metadata,
    )

    metadata["channel_index"] = 2

    assert room.metadata["channel_index"] == 1
    with pytest.raises(TypeError):
        room.metadata["channel_index"] = 3  # type: ignore[index]


def test_inbound_metadata_is_defensively_copied() -> None:
    metadata = {"stable_sender": True}
    message = InboundMessage(
        transport="fake",
        message_id="msg-1",
        sender_id="sender-1",
        channel_index=1,
        text="!ping",
        metadata=metadata,
    )

    metadata["stable_sender"] = False

    assert message.metadata["stable_sender"] is True


def test_outbound_message_keeps_legacy_reply_target_empty() -> None:
    outbound = OutboundMessage(
        destination="sender-1",
        channel_index=1,
        text="pong",
    )

    assert outbound.destination == "sender-1"
    assert outbound.channel_index == 1
    assert outbound.reply_target is None


def test_outbound_message_accepts_explicit_reply_target() -> None:
    reply_target = RoomRef.channel(transport="fake", channel_index=1)
    outbound = OutboundMessage(
        destination="sender-1",
        channel_index=1,
        text="pong",
        reply_target=reply_target,
    )

    assert outbound.reply_target == reply_target


def test_metadata_must_be_json_serializable() -> None:
    with pytest.raises(ValueError, match="JSON-serializable"):
        InboundMessage(
            transport="fake",
            message_id="msg-1",
            sender_id="sender-1",
            channel_index=1,
            text="!ping",
            metadata={"bad": object()},
        )


def test_room_kind_rejects_known_invalid_values() -> None:
    with pytest.raises(ValueError, match="room_kind"):
        RoomRef(transport="fake", room_id="fake:room:1", room_kind="telegram_chat_id")


def test_identity_kind_rejects_chat_as_normal_sender_identity() -> None:
    with pytest.raises(ValueError, match="sender identity_kind"):
        SenderIdentity(
            sender_id="telegram:chat:123",
            transport_scope="telegram",
            identity_kind="telegram_chat_id",
            stable=True,
        )
