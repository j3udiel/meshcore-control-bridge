from __future__ import annotations

import hmac
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

from meshcore_control.models import InboundMessage
from meshcore_control.storage.normalized_audit import (
    AuditKey,
    NormalizedAuditEventType,
    NormalizedAuditRepository,
)

TELEGRAM_UPDATE_DEDUP_WINDOW_SECONDS = 3600
TELEGRAM_REASONS = frozenset(
    {
        "accepted",
        "bot_message",
        "chat_not_authorized",
        "duplicate",
        "edited_message",
        "empty_text",
        "foundation_only",
        "group_ignored",
        "multimedia_ignored",
        "supergroup_ignored",
        "channel_ignored",
        "command",
        "forwarded",
        "forward_disabled",
        "rate_limited",
        "dropped",
        "failed",
        "user_not_authorized",
    }
)
BRIDGE_STATUSES = frozenset(
    {"accepted_by_meshcore_transport", "accepted_by_telegram", "observed_echo", "failed", "dropped"}
)
BRIDGE_PENDING_WINDOW_SECONDS = 600


@dataclass(frozen=True, slots=True)
class TelegramAuditRefs:
    update_ref_hash: str | None = None
    chat_ref_hash: str | None = None
    user_ref_hash: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramBridgeRecord:
    bridge_message_id: str
    correlation_id: str
    destination_transport: str
    destination_room_id: str
    content_ref_hash: str
    size_bytes: int
    status: str
    created_at: float
    expires_at: float


class TelegramStore:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        audit_key: AuditKey,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.connection = connection
        self.audit_key = audit_key
        self.clock = clock

    def is_activated(self) -> bool:
        return self._get_state("activated") == "1"

    def mark_activated(self) -> None:
        self._set_state("activated", "1")

    def last_update_id(self) -> int | None:
        value = self._get_state("last_update_id")
        return int(value) if value is not None else None

    def persist_last_update_id(self, update_id: int) -> None:
        self._set_state("last_update_id", str(update_id))

    def seen_or_store_update(self, update_id: int) -> bool:
        now = self.clock()
        self.connection.execute(
            "DELETE FROM telegram_update_deduplication WHERE expires_at <= ?",
            (now,),
        )
        update_ref = self.update_ref_hash(update_id)
        try:
            self.connection.execute(
                """
                INSERT INTO telegram_update_deduplication (update_ref_hash, expires_at)
                VALUES (?, ?)
                """,
                (update_ref, now + TELEGRAM_UPDATE_DEDUP_WINDOW_SECONDS),
            )
        except sqlite3.IntegrityError:
            self.connection.commit()
            return True
        self.connection.commit()
        return False

    def audit_event(
        self,
        *,
        event_type: str,
        reason: str,
        refs: TelegramAuditRefs,
        chat_type: str | None,
        message_type: str | None,
    ) -> None:
        if reason not in TELEGRAM_REASONS:
            raise ValueError("Telegram audit reason is not allowed")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO telegram_audit_events (
                  event_type,
                  update_ref_hash,
                  chat_ref_hash,
                  user_ref_hash,
                  chat_type,
                  message_type,
                  reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    refs.update_ref_hash,
                    refs.chat_ref_hash,
                    refs.user_ref_hash,
                    chat_type,
                    message_type,
                    reason,
                ),
            )

    def audit_bridge_event(
        self,
        *,
        repository: NormalizedAuditRepository | None,
        event_type: NormalizedAuditEventType,
        message: InboundMessage,
        correlation_id: str,
        metadata: dict[str, object],
        causation_event_id: str | None = None,
    ) -> str | None:
        if repository is None or not repository.enabled:
            return None
        event = repository.event_from_inbound(
            event_type=event_type,
            message=message,
            correlation_id=correlation_id,
            metadata=metadata,
            causation_event_id=causation_event_id,
        )
        with self.connection:
            repository.insert_event(event)
        return event.event_id

    def create_bridge_record(
        self,
        *,
        correlation_id: str,
        destination_transport: str,
        destination_room_id: str,
        content: str,
        size_bytes: int,
        status: str,
        ttl_seconds: int = BRIDGE_PENDING_WINDOW_SECONDS,
    ) -> TelegramBridgeRecord:
        if status not in BRIDGE_STATUSES:
            raise ValueError("Telegram bridge status is not allowed")
        now = self.clock()
        record = TelegramBridgeRecord(
            bridge_message_id=f"bridge:{uuid.uuid4().hex}",
            correlation_id=correlation_id,
            destination_transport=destination_transport,
            destination_room_id=destination_room_id,
            content_ref_hash=self.content_ref_hash(content),
            size_bytes=size_bytes,
            status=status,
            created_at=now,
            expires_at=now + ttl_seconds,
        )
        with self.connection:
            self.connection.execute(
                "DELETE FROM telegram_bridge_pending WHERE expires_at <= ?",
                (now,),
            )
            self.connection.execute(
                """
                INSERT INTO telegram_bridge_pending (
                  bridge_message_id,
                  correlation_id,
                  destination_transport,
                  destination_room_id,
                  content_ref_hash,
                  size_bytes,
                  status,
                  created_at,
                  expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.bridge_message_id,
                    record.correlation_id,
                    record.destination_transport,
                    record.destination_room_id,
                    record.content_ref_hash,
                    record.size_bytes,
                    record.status,
                    record.created_at,
                    record.expires_at,
                ),
            )
        return record

    def consume_pending_echo(
        self,
        *,
        destination_transport: str,
        destination_room_id: str,
        content: str,
        size_bytes: int,
    ) -> TelegramBridgeRecord | None:
        now = self.clock()
        content_ref_hash = self.content_ref_hash(content)
        with self.connection:
            self.connection.execute(
                "DELETE FROM telegram_bridge_pending WHERE expires_at <= ?",
                (now,),
            )
            row = self.connection.execute(
                """
                SELECT *
                FROM telegram_bridge_pending
                WHERE destination_transport = ?
                  AND destination_room_id = ?
                  AND content_ref_hash = ?
                  AND size_bytes = ?
                  AND status = ?
                  AND expires_at > ?
                ORDER BY created_at
                LIMIT 1
                """,
                (
                    destination_transport,
                    destination_room_id,
                    content_ref_hash,
                    size_bytes,
                    "accepted_by_meshcore_transport",
                    now,
                ),
            ).fetchone()
            if row is None:
                return None
            self.connection.execute(
                """
                UPDATE telegram_bridge_pending
                SET status = ?
                WHERE bridge_message_id = ?
                """,
                ("observed_echo", row["bridge_message_id"]),
            )
        return TelegramBridgeRecord(
            bridge_message_id=str(row["bridge_message_id"]),
            correlation_id=str(row["correlation_id"]),
            destination_transport=str(row["destination_transport"]),
            destination_room_id=str(row["destination_room_id"]),
            content_ref_hash=str(row["content_ref_hash"]),
            size_bytes=int(row["size_bytes"]),
            status="observed_echo",
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
        )

    def refs(
        self,
        *,
        update_id: int | None = None,
        chat_id: str | None = None,
        user_id: str | None = None,
    ) -> TelegramAuditRefs:
        return TelegramAuditRefs(
            update_ref_hash=self.update_ref_hash(update_id) if update_id is not None else None,
            chat_ref_hash=self.chat_ref_hash(chat_id) if chat_id else None,
            user_ref_hash=self.user_ref_hash(user_id) if user_id else None,
        )

    def update_ref_hash(self, update_id: int) -> str:
        return self._reference(f"telegram-update:v1\0{update_id}")

    def chat_ref_hash(self, chat_id: str) -> str:
        return self._reference(f"telegram-chat:v1\0{chat_id}")

    def user_ref_hash(self, user_id: str) -> str:
        return self._reference(f"telegram-user:v1\0{user_id}")

    def content_ref_hash(self, content: str) -> str:
        return self._reference(f"bridge-content:v1\0{content}")

    def _reference(self, material: str) -> str:
        digest = hmac.new(self.audit_key.key, material.encode("utf-8"), sha256).hexdigest()
        return f"hmac-sha256:v1:{self.audit_key.key_id}:{digest}"

    def _get_state(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM telegram_state WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else None

    def _set_state(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO telegram_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value = excluded.value,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )
