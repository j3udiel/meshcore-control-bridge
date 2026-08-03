from __future__ import annotations

from meshcore_control.models import RoomRef, SenderIdentity

TELEGRAM_TRANSPORT = "telegram"
TELEGRAM_ROOM_ID = "telegram:private:authorized"
TELEGRAM_SENDER_ID = "telegram-user:default:authorized"


def telegram_room() -> RoomRef:
    return RoomRef(
        transport=TELEGRAM_TRANSPORT,
        room_id=TELEGRAM_ROOM_ID,
        room_kind="telegram_chat",
        metadata={"chat_type": "private"},
    )


def telegram_sender() -> SenderIdentity:
    return SenderIdentity(
        sender_id=TELEGRAM_SENDER_ID,
        transport_scope=TELEGRAM_TRANSPORT,
        identity_kind="telegram_user_id",
        stable=True,
    )
