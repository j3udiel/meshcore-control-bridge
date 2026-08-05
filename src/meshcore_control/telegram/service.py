from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from meshcore_control.bridge_health import BridgeHealthState
from meshcore_control.commands.router import CommandRouter
from meshcore_control.config import TelegramConfig
from meshcore_control.models import InboundMessage, MessageIdentity, OutboundMessage, RoomRef
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.audit_flow import AuditFlow, AuditTrail
from meshcore_control.storage.database import is_sqlite_locked
from meshcore_control.storage.normalized_audit import (
    NormalizedAuditEventType,
    NormalizedAuditRepository,
)
from meshcore_control.telegram.client import (
    TelegramApiError,
    TelegramConflictError,
    TelegramRateLimitError,
)
from meshcore_control.telegram.identity import (
    TELEGRAM_ROOM_ID,
    TELEGRAM_SENDER_ID,
    TELEGRAM_TRANSPORT,
    telegram_room,
    telegram_sender,
)
from meshcore_control.telegram.store import TelegramAuditRefs, TelegramBridgeRecord, TelegramStore
from meshcore_control.transport.base import Transport

logger = logging.getLogger(__name__)
TELEGRAM_RESPONSE_MAX_CHARS = 3900
MESHCORE_FORWARD_SUCCESS_TEXT = "Enviado a MeshCore."
MESHCORE_FORWARD_FAILURE_TEXT = "No se pudo enviar a MeshCore."
MESHCORE_TRANSPORT_NAME = "homeassistant-meshcore"


class TelegramClientProtocol(Protocol):
    async def delete_webhook(self, *, drop_pending_updates: bool) -> None: ...

    async def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        allowed_updates: tuple[str, ...] = ("message",),
    ) -> list[dict[str, Any]]: ...

    async def send_message(self, *, chat_id: str, text: str) -> None: ...


SleepCallable = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TelegramUpdateDecision:
    update_id: int
    reason: str
    chat_type: str | None
    message_type: str | None


class MeshCoreToTelegramForwarder:
    def __init__(
        self,
        *,
        config: TelegramConfig,
        client: TelegramClientProtocol,
        store: TelegramStore,
        normalized_audit: NormalizedAuditRepository | None = None,
        bridge_health: BridgeHealthState | None = None,
        sleep: SleepCallable = asyncio.sleep,
        backoff_max_seconds: float = 30.0,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.normalized_audit = normalized_audit
        self.bridge_health = bridge_health
        self.sleep = sleep
        self.backoff_max_seconds = backoff_max_seconds
        self.rate_limiter = RateLimiter(
            max_commands=config.inbound_forwarding_rate_limit.commands,
            window_seconds=config.inbound_forwarding_rate_limit.window_seconds,
        )
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True
        if self.bridge_health is not None:
            self.bridge_health.set_telegram_polling("disconnected")

    async def forward_normal_text(
        self,
        message: InboundMessage,
        *,
        audit_trail: AuditTrail | None = None,
    ) -> bool:
        normalized_source_text = _normalized_bridge_text(message.text)
        rendered = render_telegram_forward_message(
            text=message.text,
            prefix=self.config.meshcore_to_telegram_prefix,
            max_bytes=self.config.max_telegram_message_length,
        )
        source_size_bytes = len(normalized_source_text.encode("utf-8"))
        size_bytes = len(rendered.text.encode("utf-8")) if rendered.text else 0
        received_event_id = self._audit_bridge(
            NormalizedAuditEventType.BRIDGE_MESSAGE_RECEIVED,
            message=message,
            audit_trail=audit_trail,
            metadata={
                "direction": "meshcore_to_telegram",
                "source_transport": MESHCORE_TRANSPORT_NAME,
                "destination_transport": TELEGRAM_TRANSPORT,
                "size_bytes": source_size_bytes,
            },
            causation_event_id=audit_trail.latest_event_id if audit_trail else None,
        )
        if not self.config.forward_meshcore_to_telegram:
            self._audit_ignored(
                message=message,
                audit_trail=audit_trail,
                reason="forward_disabled",
                size_bytes=size_bytes,
                truncated=rendered.truncated,
                causation_event_id=received_event_id,
            )
            logger.info("MeshCore to Telegram forward ignored reason=forward_disabled")
            return True
        if _is_self_sent_meshcore(message):
            self._audit_ignored(
                message=message,
                audit_trail=audit_trail,
                reason="loop_prevention",
                size_bytes=size_bytes,
                truncated=rendered.truncated,
                causation_event_id=received_event_id,
            )
            logger.info("MeshCore to Telegram forward ignored reason=loop_prevention")
            return True
        if rendered.text is None:
            self._audit_ignored(
                message=message,
                audit_trail=audit_trail,
                reason="dropped",
                size_bytes=0,
                truncated=rendered.truncated,
                causation_event_id=received_event_id,
            )
            _safe_create_bridge_record(
                self.store,
                bridge_health=self.bridge_health,
                correlation_id=_correlation_id(message, audit_trail),
                destination_transport=TELEGRAM_TRANSPORT,
                destination_room_id=TELEGRAM_ROOM_ID,
                content="",
                size_bytes=0,
                status="dropped",
            )
            return True
        if self._consume_pending_echo(message, normalized_source_text, source_size_bytes):
            self._audit_ignored(
                message=message,
                audit_trail=audit_trail,
                reason="loop_prevention",
                size_bytes=size_bytes,
                truncated=rendered.truncated,
                causation_event_id=received_event_id,
            )
            logger.info("MeshCore to Telegram forward ignored reason=loop_prevention")
            return True
        if not self.rate_limiter.allow(_meshcore_forward_rate_key(message)):
            self._audit_ignored(
                message=message,
                audit_trail=audit_trail,
                reason="rate_limited",
                size_bytes=size_bytes,
                truncated=rendered.truncated,
                causation_event_id=received_event_id,
            )
            _safe_create_bridge_record(
                self.store,
                bridge_health=self.bridge_health,
                correlation_id=_correlation_id(message, audit_trail),
                destination_transport=TELEGRAM_TRANSPORT,
                destination_room_id=TELEGRAM_ROOM_ID,
                content=rendered.text,
                size_bytes=size_bytes,
                status="dropped",
            )
            logger.info("MeshCore to Telegram forward ignored reason=rate_limited")
            return True
        try:
            await self.client.send_message(
                chat_id=self.config.allowed_private_chat_id,
                text=rendered.text,
            )
        except asyncio.CancelledError:
            raise
        except TelegramRateLimitError as exc:
            await self._sleep_or_stop(min(exc.retry_after, self.backoff_max_seconds))
            if self.bridge_health is not None:
                self.bridge_health.record_mc_to_tg(success=False, reason="rate_limited")
            self._record_failed(
                message=message,
                audit_trail=audit_trail,
                rendered=rendered,
                size_bytes=size_bytes,
                reason="rate_limited",
                causation_event_id=received_event_id,
            )
            logger.warning("MeshCore to Telegram forward failed reason=rate_limited")
            return True
        except TelegramConflictError:
            if self.bridge_health is not None:
                self.bridge_health.record_mc_to_tg(success=False, reason="consumer_conflict")
            self._record_failed(
                message=message,
                audit_trail=audit_trail,
                rendered=rendered,
                size_bytes=size_bytes,
                reason="consumer_conflict",
                causation_event_id=received_event_id,
            )
            logger.warning("MeshCore to Telegram forward failed reason=consumer_conflict")
            return True
        except (TelegramApiError, httpx.HTTPError, TimeoutError, OSError):
            if self.bridge_health is not None:
                self.bridge_health.record_mc_to_tg(success=False, reason="transport_error")
            self._record_failed(
                message=message,
                audit_trail=audit_trail,
                rendered=rendered,
                size_bytes=size_bytes,
                reason="transport_error",
                causation_event_id=received_event_id,
            )
            logger.warning("MeshCore to Telegram forward failed reason=transport_error")
            return True
        self._audit_bridge(
            NormalizedAuditEventType.BRIDGE_MESSAGE_FORWARDED,
            message=message,
            audit_trail=audit_trail,
            metadata={
                "direction": "meshcore_to_telegram",
                "source_transport": MESHCORE_TRANSPORT_NAME,
                "destination_transport": TELEGRAM_TRANSPORT,
                "result": "accepted_by_telegram",
                "size_bytes": size_bytes,
                "truncated": rendered.truncated,
            },
            causation_event_id=received_event_id,
        )
        _safe_create_bridge_record(
            self.store,
            bridge_health=self.bridge_health,
            correlation_id=_correlation_id(message, audit_trail),
            destination_transport=TELEGRAM_TRANSPORT,
            destination_room_id=TELEGRAM_ROOM_ID,
            content=rendered.text,
            size_bytes=size_bytes,
            status="accepted_by_telegram",
        )
        if self.bridge_health is not None:
            self.bridge_health.record_mc_to_tg(success=True)
        logger.info("MeshCore message forwarded to Telegram status=accepted_by_telegram")
        return True

    def _consume_pending_echo(
        self,
        message: InboundMessage,
        rendered_text: str,
        size_bytes: int,
    ) -> bool:
        source_room = message.source_room or _meshcore_room(message.channel_index)
        try:
            record = self.store.consume_pending_echo(
                destination_transport=MESHCORE_TRANSPORT_NAME,
                destination_room_id=source_room.room_id,
                content=rendered_text,
                size_bytes=size_bytes,
            )
        except sqlite3.Error as exc:
            if self.bridge_health is not None:
                self.bridge_health.set_telegram_db_health("degraded", reason=_sqlite_reason(exc))
            logger.warning(
                "Telegram bridge pending echo unavailable reason=%s",
                _sqlite_reason(exc),
            )
            return True
        return record is not None

    def _audit_ignored(
        self,
        *,
        message: InboundMessage,
        audit_trail: AuditTrail | None,
        reason: str,
        size_bytes: int,
        truncated: bool,
        causation_event_id: str | None,
    ) -> None:
        self._audit_bridge(
            NormalizedAuditEventType.BRIDGE_MESSAGE_IGNORED,
            message=message,
            audit_trail=audit_trail,
            metadata={
                "direction": "meshcore_to_telegram",
                "source_transport": MESHCORE_TRANSPORT_NAME,
                "destination_transport": TELEGRAM_TRANSPORT,
                "reason": reason,
                "size_bytes": size_bytes,
                "truncated": truncated,
            },
            causation_event_id=causation_event_id,
        )

    def _record_failed(
        self,
        *,
        message: InboundMessage,
        audit_trail: AuditTrail | None,
        rendered: ForwardRenderResult,
        size_bytes: int,
        reason: str,
        causation_event_id: str | None,
    ) -> None:
        self._audit_bridge(
            NormalizedAuditEventType.BRIDGE_MESSAGE_FAILED,
            message=message,
            audit_trail=audit_trail,
            metadata={
                "direction": "meshcore_to_telegram",
                "source_transport": MESHCORE_TRANSPORT_NAME,
                "destination_transport": TELEGRAM_TRANSPORT,
                "result": "failed",
                "reason": reason,
                "size_bytes": size_bytes,
                "truncated": rendered.truncated,
            },
            causation_event_id=causation_event_id,
        )
        _safe_create_bridge_record(
            self.store,
            bridge_health=self.bridge_health,
            correlation_id=_correlation_id(message, audit_trail),
            destination_transport=TELEGRAM_TRANSPORT,
            destination_room_id=TELEGRAM_ROOM_ID,
            content=rendered.text or "",
            size_bytes=size_bytes,
            status="failed",
        )

    def _audit_bridge(
        self,
        event_type: NormalizedAuditEventType,
        *,
        message: InboundMessage,
        audit_trail: AuditTrail | None,
        metadata: dict[str, object],
        causation_event_id: str | None = None,
    ) -> str | None:
        try:
            return self.store.audit_bridge_event(
                repository=self.normalized_audit,
                event_type=event_type,
                message=message,
                correlation_id=_correlation_id(message, audit_trail),
                metadata=metadata,
                causation_event_id=causation_event_id,
            )
        except sqlite3.Error as exc:
            if self.bridge_health is not None:
                self.bridge_health.set_audit_db_health("degraded", reason=_sqlite_reason(exc))
            logger.warning("Bridge audit event skipped reason=%s", _sqlite_reason(exc))
            return None

    async def _sleep_or_stop(self, delay: float) -> None:
        if self._stopping:
            return
        await self.sleep(delay)


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
        router: CommandRouter | None = None,
        audit_flow: AuditFlow | None = None,
        meshcore_transport: Transport | None = None,
        normalized_audit: NormalizedAuditRepository | None = None,
        bridge_health: BridgeHealthState | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.poll_timeout_seconds = poll_timeout_seconds
        self.backoff_initial_seconds = backoff_initial_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self.sleep = sleep
        self.router = router
        self.audit_flow = audit_flow
        self.meshcore_transport = meshcore_transport
        self.normalized_audit = normalized_audit
        self.bridge_health = bridge_health
        self.forwarding_rate_limiter = RateLimiter(
            max_commands=config.forwarding_rate_limit.commands,
            window_seconds=config.forwarding_rate_limit.window_seconds,
        )
        self._stopping = False

    async def run(self) -> None:
        logger.info("Telegram foundation enabled")
        if self.bridge_health is not None:
            self.bridge_health.set_telegram_polling("disconnected")
        await self.initialize()
        backoff = self.backoff_initial_seconds
        while not self._stopping:
            try:
                await self.poll_once()
                if self.bridge_health is not None:
                    self.bridge_health.set_telegram_polling("connected")
                backoff = self.backoff_initial_seconds
            except TelegramRateLimitError as exc:
                if self.bridge_health is not None:
                    self.bridge_health.set_telegram_polling("degraded")
                    self.bridge_health.record_failure("rate_limited")
                delay = min(exc.retry_after, self.backoff_max_seconds)
                logger.warning("Telegram polling delayed reason=rate_limited")
                await self._sleep_or_stop(delay)
            except TelegramConflictError:
                if self.bridge_health is not None:
                    self.bridge_health.set_telegram_polling("degraded")
                    self.bridge_health.record_failure("consumer_conflict")
                logger.error("Telegram polling stopped reason=another_consumer")
                raise
            except (TelegramApiError, httpx.HTTPError, TimeoutError, OSError):
                if self.bridge_health is not None:
                    self.bridge_health.set_telegram_polling("degraded")
                    self.bridge_health.record_failure("transport_error")
                logger.warning("Telegram polling retry scheduled reason=transport_error")
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, self.backoff_max_seconds)

    def stop(self) -> None:
        self._stopping = True
        if self.bridge_health is not None:
            self.bridge_health.set_telegram_polling("disconnected")

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
            decision = await self.process_update(update)
            decisions.append(decision)
            try:
                self.store.persist_last_update_id(decision.update_id)
            except sqlite3.Error as exc:
                if self.bridge_health is not None:
                    self.bridge_health.set_telegram_db_health(
                        "degraded",
                        reason=_sqlite_reason(exc),
                    )
                logger.warning(
                    "Telegram update offset not persisted reason=%s",
                    _sqlite_reason(exc),
                )
        return decisions

    async def process_update(self, update: dict[str, Any]) -> TelegramUpdateDecision:
        update_id = _update_id(update)
        if isinstance(update.get("edited_message"), dict):
            refs = self.store.refs(update_id=update_id)
            if self._seen_or_store_update(update_id):
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

        if self._seen_or_store_update(update_id):
            self._audit("telegram.update.ignored", "duplicate", refs, chat_type, None)
            return TelegramUpdateDecision(update_id, "duplicate", chat_type, None)

        reason, message_type = self._classify(message, chat_id=chat_id, user_id=user_id)
        text = message.get("text") if isinstance(message, dict) else None
        if (
            reason == "foundation_only"
            and message is not None
            and isinstance(text, str)
            and text.strip().startswith(self.config.command_prefix)
        ):
            return await self._process_command_update(
                update_id=update_id,
                message=message,
                refs=refs,
                chat_type=chat_type,
            )
        if reason == "foundation_only" and message is not None and isinstance(text, str):
            return await self._process_forward_update(
                update_id=update_id,
                message=message,
                refs=refs,
                chat_type=chat_type,
                text=text,
            )
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

    async def _process_command_update(
        self,
        *,
        update_id: int,
        message: dict[str, Any],
        refs: TelegramAuditRefs,
        chat_type: str | None,
    ) -> TelegramUpdateDecision:
        text = self._safe_command_text(str(message.get("text", "")))
        inbound = self._inbound_message(update_id=update_id, text=text, refs=refs)
        audit_trail = self._message_received(inbound)
        response_text: str | None = None
        try:
            if self.router is None:
                response_text = "No autorizado."
            else:
                response_text = await self.router.handle(inbound, audit_trail=audit_trail)
            if response_text is not None:
                await self._send_command_response(
                    chat_id=self.config.allowed_private_chat_id,
                    text=response_text,
                    audit_trail=audit_trail,
                    inbound=inbound,
                )
        except TelegramConflictError:
            raise
        except (TelegramApiError, httpx.HTTPError, TimeoutError, OSError):
            logger.warning("Telegram command response failed reason=transport_error")
        self._audit("telegram.update.accepted", "command", refs, chat_type, "text")
        logger.info(
            "Telegram update classified reason=%s chat_type=%s message_type=%s",
            "command",
            chat_type or "unknown",
            "text",
        )
        return TelegramUpdateDecision(update_id, "command", chat_type, "text")

    async def _process_forward_update(
        self,
        *,
        update_id: int,
        message: dict[str, Any],
        refs: TelegramAuditRefs,
        chat_type: str | None,
        text: str,
    ) -> TelegramUpdateDecision:
        inbound = self._inbound_message(update_id=update_id, text="[telegram-text]", refs=refs)
        rendered = render_meshcore_forward_message(
            text=text,
            prefix=self.config.message_prefix,
            max_bytes=self.config.max_meshcore_message_length,
        )
        size_bytes = len(rendered.text.encode("utf-8")) if rendered.text else 0
        received_event_id = self._audit_bridge(
            NormalizedAuditEventType.BRIDGE_MESSAGE_RECEIVED,
            inbound=inbound,
            metadata={
                "direction": "telegram_to_meshcore",
                "source_transport": TELEGRAM_TRANSPORT,
                "destination_transport": MESHCORE_TRANSPORT_NAME,
                "size_bytes": len((self.config.message_prefix + text.strip()).encode("utf-8")),
            },
        )
        if not self.config.forward_telegram_to_meshcore:
            self._audit_bridge(
                NormalizedAuditEventType.BRIDGE_MESSAGE_IGNORED,
                inbound=inbound,
                metadata={
                    "direction": "telegram_to_meshcore",
                    "source_transport": TELEGRAM_TRANSPORT,
                    "destination_transport": MESHCORE_TRANSPORT_NAME,
                    "reason": "forward_disabled",
                    "size_bytes": size_bytes,
                    "truncated": rendered.truncated,
                },
                causation_event_id=received_event_id,
            )
            self._audit("telegram.update.ignored", "forward_disabled", refs, chat_type, "text")
            logger.info(
                "Telegram update classified reason=%s chat_type=%s message_type=%s",
                "forward_disabled",
                chat_type or "unknown",
                "text",
            )
            return TelegramUpdateDecision(update_id, "forward_disabled", chat_type, "text")
        if rendered.text is None:
            self._audit_bridge(
                NormalizedAuditEventType.BRIDGE_MESSAGE_IGNORED,
                inbound=inbound,
                metadata={
                    "direction": "telegram_to_meshcore",
                    "source_transport": TELEGRAM_TRANSPORT,
                    "destination_transport": MESHCORE_TRANSPORT_NAME,
                    "reason": "dropped",
                    "size_bytes": 0,
                    "truncated": rendered.truncated,
                },
                causation_event_id=received_event_id,
            )
            _safe_create_bridge_record(
                self.store,
                bridge_health=self.bridge_health,
                correlation_id=inbound.message.correlation_id if inbound.message else "",
                destination_transport=MESHCORE_TRANSPORT_NAME,
                destination_room_id=_meshcore_room(self.config.meshcore_channel_index).room_id,
                content="",
                size_bytes=0,
                status="dropped",
            )
            self._audit("telegram.update.ignored", "dropped", refs, chat_type, "text")
            return TelegramUpdateDecision(update_id, "dropped", chat_type, "text")
        if not self.forwarding_rate_limiter.allow(f"{TELEGRAM_SENDER_ID}:{TELEGRAM_ROOM_ID}"):
            self._audit_bridge(
                NormalizedAuditEventType.BRIDGE_MESSAGE_IGNORED,
                inbound=inbound,
                metadata={
                    "direction": "telegram_to_meshcore",
                    "source_transport": TELEGRAM_TRANSPORT,
                    "destination_transport": MESHCORE_TRANSPORT_NAME,
                    "reason": "rate_limited",
                    "size_bytes": size_bytes,
                    "truncated": rendered.truncated,
                },
                causation_event_id=received_event_id,
            )
            _safe_create_bridge_record(
                self.store,
                bridge_health=self.bridge_health,
                correlation_id=inbound.message.correlation_id if inbound.message else "",
                destination_transport=MESHCORE_TRANSPORT_NAME,
                destination_room_id=_meshcore_room(self.config.meshcore_channel_index).room_id,
                content=rendered.text,
                size_bytes=size_bytes,
                status="dropped",
            )
            self._audit("telegram.update.ignored", "rate_limited", refs, chat_type, "text")
            await self._send_forward_confirmation(
                chat_id=self.config.allowed_private_chat_id,
                text="Rate limit.",
            )
            return TelegramUpdateDecision(update_id, "rate_limited", chat_type, "text")
        if self.meshcore_transport is None:
            if self.bridge_health is not None:
                self.bridge_health.record_tg_to_mc(success=False, reason="transport_error")
            await self._record_forward_failure(
                inbound=inbound,
                rendered=rendered,
                size_bytes=size_bytes,
                reason="transport_unavailable",
                causation_event_id=received_event_id,
            )
            self._audit("telegram.update.ignored", "failed", refs, chat_type, "text")
            await self._send_forward_confirmation(
                chat_id=self.config.allowed_private_chat_id,
                text=MESHCORE_FORWARD_FAILURE_TEXT,
            )
            return TelegramUpdateDecision(update_id, "failed", chat_type, "text")
        outbound = OutboundMessage(
            destination=_meshcore_room(self.config.meshcore_channel_index).room_id,
            channel_index=self.config.meshcore_channel_index,
            text=rendered.text,
            reply_to=inbound.message_id,
            reply_target=_meshcore_room(self.config.meshcore_channel_index),
            metadata={"source_transport": TELEGRAM_TRANSPORT},
        )
        try:
            await self.meshcore_transport.send(outbound)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Telegram to MeshCore forward failed reason=transport_error")
            if self.bridge_health is not None:
                self.bridge_health.record_tg_to_mc(success=False, reason="transport_error")
            await self._record_forward_failure(
                inbound=inbound,
                rendered=rendered,
                size_bytes=size_bytes,
                reason="transport_error",
                causation_event_id=received_event_id,
            )
            self._audit("telegram.update.ignored", "failed", refs, chat_type, "text")
            await self._send_forward_confirmation(
                chat_id=self.config.allowed_private_chat_id,
                text=MESHCORE_FORWARD_FAILURE_TEXT,
            )
            return TelegramUpdateDecision(update_id, "failed", chat_type, "text")
        forwarded_event_id = self._audit_bridge(
            NormalizedAuditEventType.BRIDGE_MESSAGE_FORWARDED,
            inbound=inbound,
            metadata={
                "direction": "telegram_to_meshcore",
                "source_transport": TELEGRAM_TRANSPORT,
                "destination_transport": MESHCORE_TRANSPORT_NAME,
                "result": "accepted_by_meshcore_transport",
                "size_bytes": size_bytes,
                "truncated": rendered.truncated,
            },
            causation_event_id=received_event_id,
        )
        record = _safe_create_bridge_record(
            self.store,
            bridge_health=self.bridge_health,
            correlation_id=inbound.message.correlation_id if inbound.message else "",
            destination_transport=MESHCORE_TRANSPORT_NAME,
            destination_room_id=outbound.reply_target.room_id if outbound.reply_target else "",
            content=rendered.text,
            size_bytes=size_bytes,
            status="accepted_by_meshcore_transport",
        )
        if self.bridge_health is not None:
            self.bridge_health.record_tg_to_mc(success=True)
        logger.info(
            "Telegram message forwarded to MeshCore channel=%s status=%s",
            self.config.meshcore_channel_index,
            record.status if record is not None else "accepted_by_meshcore_transport",
        )
        self._audit("telegram.update.accepted", "forwarded", refs, chat_type, "text")
        if self.config.send_forward_confirmation:
            await self._send_forward_confirmation(
                chat_id=self.config.allowed_private_chat_id,
                text=MESHCORE_FORWARD_SUCCESS_TEXT,
            )
        else:
            logger.info("Telegram forward confirmation skipped reason=disabled")
        if forwarded_event_id is not None:
            logger.info("Telegram bridge event recorded status=accepted_by_meshcore_transport")
        return TelegramUpdateDecision(update_id, "forwarded", chat_type, "text")

    async def _send_command_response(
        self,
        *,
        chat_id: str,
        text: str,
        audit_trail: AuditTrail | None,
        inbound: InboundMessage,
    ) -> None:
        outbound = OutboundMessage(
            destination=TELEGRAM_SENDER_ID,
            channel_index=self.config.meshcore_channel_index,
            text=_trim_telegram_response(text),
            reply_to=inbound.message_id,
            reply_target=inbound.reply_target,
        )
        try:
            await self.client.send_message(chat_id=chat_id, text=outbound.text)
        except TelegramRateLimitError as exc:
            if self.audit_flow is not None and audit_trail is not None:
                self.audit_flow.response_failed(audit_trail)
            await self._sleep_or_stop(min(exc.retry_after, self.backoff_max_seconds))
            raise
        except Exception:
            if self.audit_flow is not None and audit_trail is not None:
                self.audit_flow.response_failed(audit_trail)
            raise
        if self.audit_flow is not None and audit_trail is not None:
            self.audit_flow.response_sent(audit_trail, outbound)

    def _inbound_message(
        self,
        *,
        update_id: int,
        text: str,
        refs: TelegramAuditRefs,
    ) -> InboundMessage:
        room = telegram_room()
        platform_message_id = refs.update_ref_hash or f"telegram-update:{update_id}"
        message_identity = MessageIdentity.from_message_id(
            transport=TELEGRAM_TRANSPORT,
            room_id=room.room_id,
            message_id=platform_message_id,
        )
        return InboundMessage(
            transport=TELEGRAM_TRANSPORT,
            message_id=platform_message_id,
            sender_id=TELEGRAM_SENDER_ID,
            channel_index=self.config.meshcore_channel_index,
            text=text,
            received_at=datetime.now(UTC),
            metadata={"message_type": "text", "chat_type": "private"},
            source_room=room,
            reply_target=room,
            sender=telegram_sender(),
            message=message_identity,
        )

    async def _record_forward_failure(
        self,
        *,
        inbound: InboundMessage,
        rendered: ForwardRenderResult,
        size_bytes: int,
        reason: str,
        causation_event_id: str | None,
    ) -> None:
        self._audit_bridge(
            NormalizedAuditEventType.BRIDGE_MESSAGE_FAILED,
            inbound=inbound,
            metadata={
                "direction": "telegram_to_meshcore",
                "source_transport": TELEGRAM_TRANSPORT,
                "destination_transport": MESHCORE_TRANSPORT_NAME,
                "result": "failed",
                "reason": reason,
                "size_bytes": size_bytes,
                "truncated": rendered.truncated,
            },
            causation_event_id=causation_event_id,
        )
        _safe_create_bridge_record(
            self.store,
            bridge_health=self.bridge_health,
            correlation_id=inbound.message.correlation_id if inbound.message else "",
            destination_transport=MESHCORE_TRANSPORT_NAME,
            destination_room_id=_meshcore_room(self.config.meshcore_channel_index).room_id,
            content=rendered.text or "",
            size_bytes=size_bytes,
            status="failed",
        )

    def _audit_bridge(
        self,
        event_type: NormalizedAuditEventType,
        *,
        inbound: InboundMessage,
        metadata: dict[str, object],
        causation_event_id: str | None = None,
    ) -> str | None:
        try:
            return self.store.audit_bridge_event(
                repository=self.normalized_audit,
                event_type=event_type,
                message=inbound,
                correlation_id=inbound.message.correlation_id if inbound.message else "",
                metadata=metadata,
                causation_event_id=causation_event_id,
            )
        except sqlite3.Error as exc:
            if self.bridge_health is not None:
                self.bridge_health.set_audit_db_health("degraded", reason=_sqlite_reason(exc))
            logger.warning("Bridge audit event skipped reason=%s", _sqlite_reason(exc))
            return None

    def _message_received(self, inbound: InboundMessage) -> AuditTrail | None:
        if self.audit_flow is None:
            return None
        try:
            return self.audit_flow.message_received(inbound)
        except sqlite3.Error as exc:
            if self.bridge_health is not None:
                self.bridge_health.set_audit_db_health("degraded", reason=_sqlite_reason(exc))
            logger.warning("Audit degraded stage=message_received error=%s", _sqlite_reason(exc))
            return self.audit_flow.degraded_trail(inbound)
        except RuntimeError:
            logger.warning("Audit degraded stage=message_received error=storage_error")
            return self.audit_flow.degraded_trail(inbound)

    async def _send_forward_confirmation(self, *, chat_id: str, text: str) -> None:
        try:
            await self.client.send_message(chat_id=chat_id, text=text)
        except TelegramRateLimitError as exc:
            await self._sleep_or_stop(min(exc.retry_after, self.backoff_max_seconds))
            logger.warning("Telegram forward confirmation failed reason=rate_limited")
        except TelegramConflictError:
            logger.warning("Telegram forward confirmation failed reason=consumer_conflict")
        except (TelegramApiError, httpx.HTTPError, TimeoutError, OSError):
            logger.warning("Telegram forward confirmation failed reason=transport_error")

    def _safe_command_text(self, text: str) -> str:
        stripped = text.strip()
        command_token = stripped.split(maxsplit=1)[0]
        command_name = command_token.removeprefix(self.config.command_prefix).lower()
        if self.router is None or self.router.registry.resolve(command_name) is None:
            return f"{self.config.command_prefix}unknown"
        if command_name == "estado":
            args = stripped.split()
            if len(args) > 1 and args[1].lower() == "ha":
                return f"{self.config.command_prefix}estado ha"
            return f"{self.config.command_prefix}estado"
        return f"{self.config.command_prefix}{command_name}"

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
        try:
            self.store.audit_event(
                event_type=event_type,
                reason=reason,
                refs=refs,
                chat_type=chat_type,
                message_type=message_type,
            )
        except sqlite3.Error as exc:
            if self.bridge_health is not None:
                self.bridge_health.set_telegram_db_health("degraded", reason=_sqlite_reason(exc))
            logger.warning("Telegram audit event skipped reason=%s", _sqlite_reason(exc))

    def _seen_or_store_update(self, update_id: int) -> bool:
        try:
            return self.store.seen_or_store_update(update_id)
        except sqlite3.Error as exc:
            if self.bridge_health is not None:
                self.bridge_health.set_telegram_db_health("degraded", reason=_sqlite_reason(exc))
            logger.warning("Telegram update ignored reason=%s", _sqlite_reason(exc))
            return True

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


def _trim_telegram_response(text: str) -> str:
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(normalized) <= TELEGRAM_RESPONSE_MAX_CHARS:
        return normalized
    return normalized[: TELEGRAM_RESPONSE_MAX_CHARS - 14].rstrip() + "\n... truncado"


@dataclass(frozen=True, slots=True)
class ForwardRenderResult:
    text: str | None
    truncated: bool


def render_meshcore_forward_message(
    *,
    text: str,
    prefix: str,
    max_bytes: int,
) -> ForwardRenderResult:
    normalized_text = _normalized_bridge_text(text)
    if not normalized_text:
        return ForwardRenderResult(text=None, truncated=False)
    candidate = prefix + normalized_text
    if len(candidate.encode("utf-8")) <= max_bytes:
        return ForwardRenderResult(text=candidate, truncated=False)
    truncated = _truncate_utf8_words(candidate, max_bytes)
    if truncated is None:
        return ForwardRenderResult(text=None, truncated=True)
    return ForwardRenderResult(text=truncated, truncated=True)


def render_telegram_forward_message(
    *,
    text: str,
    prefix: str,
    max_bytes: int,
) -> ForwardRenderResult:
    normalized_text = _normalized_bridge_text(text)
    if not normalized_text:
        return ForwardRenderResult(text=None, truncated=False)
    candidate = prefix + normalized_text
    if len(candidate.encode("utf-8")) <= max_bytes:
        return ForwardRenderResult(text=candidate, truncated=False)
    truncated = _truncate_utf8_with_suffix(candidate, max_bytes, suffix="... truncado")
    if truncated is None:
        return ForwardRenderResult(text=None, truncated=True)
    return ForwardRenderResult(text=truncated, truncated=True)


def _truncate_utf8_words(value: str, max_bytes: int) -> str | None:
    ellipsis = "..."
    ellipsis_bytes = len(ellipsis.encode("utf-8"))
    if max_bytes < ellipsis_bytes + 1:
        return None
    budget = max_bytes - ellipsis_bytes
    words = value.split(" ")
    output = ""
    for word in words:
        candidate = word if not output else f"{output} {word}"
        if len(candidate.encode("utf-8")) <= budget:
            output = candidate
            continue
        break
    if not output:
        output = _truncate_utf8_boundary(value, budget).rstrip()
    output = output.rstrip()
    if not output:
        return None
    return output + ellipsis


def _truncate_utf8_with_suffix(value: str, max_bytes: int, *, suffix: str) -> str | None:
    suffix_bytes = len(suffix.encode("utf-8"))
    if max_bytes < suffix_bytes + 1:
        return None
    budget = max_bytes - suffix_bytes
    output = _truncate_utf8_boundary(value, budget).rstrip()
    if not output:
        return None
    return output + suffix


def _truncate_utf8_boundary(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _normalized_bridge_text(text: str) -> str:
    return " ".join(text.strip().split())


def _meshcore_room(channel_index: int) -> RoomRef:
    return RoomRef.channel(transport=MESHCORE_TRANSPORT_NAME, channel_index=channel_index)


def _correlation_id(message: InboundMessage, audit_trail: AuditTrail | None) -> str:
    if audit_trail is not None:
        return audit_trail.correlation_id
    if message.message is not None:
        return message.message.correlation_id
    return ""


def _meshcore_forward_rate_key(message: InboundMessage) -> str:
    room_id = (
        message.source_room.room_id
        if message.source_room
        else f"channel:{message.channel_index}"
    )
    sender_id = message.sender.sender_id if message.sender else message.sender_id
    return f"{room_id}:{sender_id}"


def _is_self_sent_meshcore(message: InboundMessage) -> bool:
    metadata = message.metadata
    if metadata.get("outgoing") is True or metadata.get("self_sent") is True:
        return True
    direction = metadata.get("direction")
    if isinstance(direction, str) and direction.lower() in {"outgoing", "sent", "tx"}:
        return True
    source = metadata.get("source_transport")
    return isinstance(source, str) and source == TELEGRAM_TRANSPORT


def _safe_create_bridge_record(
    store: TelegramStore,
    *,
    bridge_health: BridgeHealthState | None = None,
    correlation_id: str,
    destination_transport: str,
    destination_room_id: str,
    content: str,
    size_bytes: int,
    status: str,
) -> TelegramBridgeRecord | None:
    try:
        return store.create_bridge_record(
            correlation_id=correlation_id,
            destination_transport=destination_transport,
            destination_room_id=destination_room_id,
            content=content,
            size_bytes=size_bytes,
            status=status,
        )
    except sqlite3.Error as exc:
        if bridge_health is not None:
            bridge_health.set_telegram_db_health("degraded", reason=_sqlite_reason(exc))
        logger.warning("Telegram bridge pending record skipped reason=%s", _sqlite_reason(exc))
        return None


def _sqlite_reason(exc: sqlite3.Error) -> str:
    return "database_locked" if is_sqlite_locked(exc) else "storage_error"
