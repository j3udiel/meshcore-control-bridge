from __future__ import annotations

import hmac
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

from meshcore_control.storage.normalized_audit import AuditKey

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
        "user_not_authorized",
    }
)


@dataclass(frozen=True, slots=True)
class TelegramAuditRefs:
    update_ref_hash: str | None = None
    chat_ref_hash: str | None = None
    user_ref_hash: str | None = None


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
