from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from meshcore_control.config import TelegramConfig
from meshcore_control.telegram.client import (
    TelegramApiError,
    TelegramConflictError,
    TelegramRateLimitError,
)
from meshcore_control.telegram.store import TelegramAuditRefs, TelegramStore

logger = logging.getLogger(__name__)


class TelegramClientProtocol(Protocol):
    async def delete_webhook(self, *, drop_pending_updates: bool) -> None: ...

    async def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        allowed_updates: tuple[str, ...] = ("message",),
    ) -> list[dict[str, Any]]: ...


SleepCallable = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TelegramUpdateDecision:
    update_id: int
    reason: str
    chat_type: str | None
    message_type: str | None


class TelegramFoundationService:
    def __init__(
        self,
        *,
        config: TelegramConfig,
        client: TelegramClientProtocol,
        store: TelegramStore,
        poll_timeout_seconds: int = 50,
        backoff_initial_seconds: float = 1.0,
        backoff_max_seconds: float = 30.0,
        sleep: SleepCallable = asyncio.sleep,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.poll_timeout_seconds = poll_timeout_seconds
        self.backoff_initial_seconds = backoff_initial_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self.sleep = sleep
        self._stopping = False

    async def run(self) -> None:
        logger.info("Telegram foundation enabled")
        await self.initialize()
        backoff = self.backoff_initial_seconds
        while not self._stopping:
            try:
                await self.poll_once()
                backoff = self.backoff_initial_seconds
            except TelegramRateLimitError as exc:
                delay = min(exc.retry_after, self.backoff_max_seconds)
                logger.warning("Telegram polling delayed reason=rate_limited")
                await self._sleep_or_stop(delay)
            except TelegramConflictError:
                logger.error("Telegram polling stopped reason=another_consumer")
                raise
            except (TelegramApiError, httpx.HTTPError, TimeoutError, OSError):
                logger.warning("Telegram polling retry scheduled reason=transport_error")
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, self.backoff_max_seconds)

    def stop(self) -> None:
        self._stopping = True

    async def initialize(self) -> None:
        if not self.store.is_activated():
            logger.info("Telegram first activation: dropping pending updates")
            await self.client.delete_webhook(drop_pending_updates=True)
            self.store.mark_activated()

    async def poll_once(self) -> list[TelegramUpdateDecision]:
        last_update_id = self.store.last_update_id()
        updates = await self.client.get_updates(
            offset=last_update_id + 1 if last_update_id is not None else None,
            timeout=self.poll_timeout_seconds,
            allowed_updates=("message",),
        )
        decisions: list[TelegramUpdateDecision] = []
        for update in updates:
            decision = self.process_update(update)
            decisions.append(decision)
            self.store.persist_last_update_id(decision.update_id)
        return decisions

    def process_update(self, update: dict[str, Any]) -> TelegramUpdateDecision:
        update_id = _update_id(update)
        if isinstance(update.get("edited_message"), dict):
            refs = self.store.refs(update_id=update_id)
            if self.store.seen_or_store_update(update_id):
                self._audit("telegram.update.ignored", "duplicate", refs, None, None)
                return TelegramUpdateDecision(update_id, "duplicate", None, None)
            self._audit("telegram.update.ignored", "edited_message", refs, None, "edited")
            logger.info(
                "Telegram update classified reason=%s chat_type=%s message_type=%s",
                "edited_message",
                "unknown",
                "edited",
            )
            return TelegramUpdateDecision(update_id, "edited_message", None, "edited")
        message = _message(update)
        chat = _mapping(message.get("chat")) if message else {}
        sender = _mapping(message.get("from")) if message else {}
        chat_id = _id_text(chat.get("id"))
        user_id = _id_text(sender.get("id"))
        chat_type = str(chat.get("type", "")) if chat else None
        refs = self.store.refs(update_id=update_id, chat_id=chat_id, user_id=user_id)

        if self.store.seen_or_store_update(update_id):
            self._audit("telegram.update.ignored", "duplicate", refs, chat_type, None)
            return TelegramUpdateDecision(update_id, "duplicate", chat_type, None)

        reason, message_type = self._classify(message, chat_id=chat_id, user_id=user_id)
        event_type = (
            "telegram.update.accepted"
            if reason == "foundation_only"
            else "telegram.update.ignored"
        )
        self._audit(event_type, reason, refs, chat_type, message_type)
        logger.info(
            "Telegram update classified reason=%s chat_type=%s message_type=%s",
            reason,
            chat_type or "unknown",
            message_type or "unknown",
        )
        return TelegramUpdateDecision(update_id, reason, chat_type, message_type)

    def _classify(
        self,
        message: dict[str, Any] | None,
        *,
        chat_id: str | None,
        user_id: str | None,
    ) -> tuple[str, str | None]:
        if message is None:
            return "multimedia_ignored", "unsupported"
        chat = _mapping(message.get("chat"))
        sender = _mapping(message.get("from"))
        chat_type = str(chat.get("type", ""))
        if chat_type == "group":
            return "group_ignored", "text" if "text" in message else "unsupported"
        if chat_type == "supergroup":
            return "supergroup_ignored", "text" if "text" in message else "unsupported"
        if chat_type == "channel":
            return "channel_ignored", "text" if "text" in message else "unsupported"
        if chat_type != "private":
            return "chat_not_authorized", "text" if "text" in message else "unsupported"
        if chat_id != self.config.allowed_private_chat_id:
            return "chat_not_authorized", "text" if "text" in message else "unsupported"
        if bool(sender.get("is_bot", False)):
            return "bot_message", "text" if "text" in message else "unsupported"
        if user_id != self.config.allowed_user_id:
            return "user_not_authorized", "text" if "text" in message else "unsupported"
        text = message.get("text")
        if text is None:
            return "multimedia_ignored", "unsupported"
        if not isinstance(text, str) or not text.strip():
            return "empty_text", "text"
        return "foundation_only", "text"

    def _audit(
        self,
        event_type: str,
        reason: str,
        refs: TelegramAuditRefs,
        chat_type: str | None,
        message_type: str | None,
    ) -> None:
        self.store.audit_event(
            event_type=event_type,
            reason=reason,
            refs=refs,
            chat_type=chat_type,
            message_type=message_type,
        )

    async def _sleep_or_stop(self, delay: float) -> None:
        if self._stopping:
            return
        await self.sleep(delay)


def _update_id(update: dict[str, Any]) -> int:
    value = update.get("update_id")
    if not isinstance(value, int):
        raise ValueError("Telegram update_id is required")
    return value


def _message(update: dict[str, Any]) -> dict[str, Any] | None:
    value = update.get("message")
    return value if isinstance(value, dict) else None


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _id_text(value: object) -> str | None:
    if isinstance(value, int | str):
        return str(value)
    return None
