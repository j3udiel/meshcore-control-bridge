from __future__ import annotations

import logging
import sqlite3
from asyncio import TimeoutError as AsyncTimeoutError
from typing import Protocol

from meshcore_control.auth.roles import Role
from meshcore_control.bridge_health import BridgeHealthState
from meshcore_control.commands.router import CommandRouter
from meshcore_control.models import InboundMessage, OutboundMessage
from meshcore_control.security.deduplication import Deduplicator
from meshcore_control.security.rate_limit import RateLimiter
from meshcore_control.storage.audit_flow import AuditFlow, AuditTrail
from meshcore_control.transport.base import Transport

logger = logging.getLogger(__name__)


class NormalTextForwarder(Protocol):
    async def forward_normal_text(
        self,
        message: InboundMessage,
        *,
        audit_trail: AuditTrail | None = None,
    ) -> bool: ...


class BridgeService:
    def __init__(
        self,
        *,
        transport: Transport,
        router: CommandRouter,
        deduplicator: Deduplicator,
        audit_flow: AuditFlow | None = None,
        rate_limiter: RateLimiter | None = None,
        channel_index: int,
        normal_text_forwarder: NormalTextForwarder | None = None,
        bridge_health: BridgeHealthState | None = None,
        meshcore_response_max_bytes: int = 180,
    ) -> None:
        self.transport = transport
        self.router = router
        self.deduplicator = deduplicator
        self.audit_flow = audit_flow
        self.rate_limiter = rate_limiter or RateLimiter()
        self.channel_index = channel_index
        self.normal_text_forwarder = normal_text_forwarder
        self.bridge_health = bridge_health
        self.meshcore_response_max_bytes = meshcore_response_max_bytes
        self._closed = False

    async def process_message(self, message: InboundMessage) -> OutboundMessage | None:
        audit_trail = self._audit_message_received(message)
        if message.channel_index != self.channel_index:
            logger.info("Message ignored reason=wrong_channel channel=%s", message.channel_index)
            self._audit_message_ignored(audit_trail, reason="wrong_channel")
            return None
        if not self.router.authorizer.allows_room(message):
            room_id = message.source_room.room_id if message.source_room is not None else "unknown"
            logger.info("Message ignored reason=room_not_allowed room=%s", room_id)
            self._audit_message_ignored(audit_trail, reason="room_not_allowed")
            return None
        if self.deduplicator.seen_or_store(message):
            logger.info("Message ignored reason=duplicate channel=%s", message.channel_index)
            self._audit_message_ignored(audit_trail, reason="duplicate")
            return None
        if not self.rate_limiter.allow(message.sender_id):
            logger.warning("Message rejected reason=rate_limited channel=%s", message.channel_index)
            audit_trail = self._audit_message_ignored(audit_trail, reason="rate_limited")
            outbound = OutboundMessage(
                destination=message.sender_id,
                channel_index=self.channel_index,
                text="Rate limit.",
                reply_to=message.message_id,
                reply_target=message.reply_target,
            )
            try:
                await self.transport.send(outbound)
            except Exception as exc:
                self._handle_response_send_failure(audit_trail, exc)
                return outbound
            self._audit_response_sent(audit_trail, outbound)
            return outbound
        response_text = await self.router.handle(message, audit_trail=audit_trail)
        if response_text is None:
            if self.normal_text_forwarder is not None:
                if self.router.authorizer.require_message(message, Role.readonly) is None:
                    logger.info(
                        "Message ignored reason=sender_not_registered channel=%s",
                        message.channel_index,
                    )
                    self._audit_message_ignored(audit_trail, reason="sender_not_registered")
                    return None
                handled = await self.normal_text_forwarder.forward_normal_text(
                    message,
                    audit_trail=audit_trail,
                )
                if handled:
                    return None
            self._audit_message_ignored(audit_trail, reason="not_a_command")
            return None
        outbound = OutboundMessage(
            destination=message.sender_id,
            channel_index=self.channel_index,
            text=_trim_lora_response(response_text, max_bytes=self.meshcore_response_max_bytes),
            reply_to=message.message_id,
            reply_target=message.reply_target,
        )
        try:
            await self.transport.send(outbound)
        except Exception as exc:
            self._handle_response_send_failure(audit_trail, exc)
            return outbound
        self._audit_response_sent(audit_trail, outbound)
        logger.info("Response sent channel=%s", outbound.channel_index)
        return outbound

    async def run_forever(self) -> None:
        try:
            while True:
                message = await self.transport.receive()
                await self.process_message(message)
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.transport.close()

    def _audit_message_received(self, message: InboundMessage) -> AuditTrail | None:
        if self.audit_flow is None:
            return None
        try:
            return self.audit_flow.message_received(message)
        except sqlite3.Error as exc:
            self._mark_audit_degraded(exc)
            logger.warning(
                "Audit degraded stage=message_received error=%s",
                _sqlite_error_reason(exc),
            )
            return self.audit_flow.degraded_trail(message)
        except Exception as exc:
            logger.warning(
                "Audit degraded stage=message_received error=%s",
                exc.__class__.__name__,
            )
            return self.audit_flow.degraded_trail(message)

    def _audit_message_ignored(
        self,
        audit_trail: AuditTrail | None,
        *,
        reason: str,
    ) -> AuditTrail | None:
        if self.audit_flow is None or audit_trail is None:
            return audit_trail
        try:
            return self.audit_flow.message_ignored(audit_trail, reason=reason)
        except sqlite3.Error as exc:
            self._mark_audit_degraded(exc)
            logger.warning(
                "Audit degraded stage=message_ignored error=%s",
                _sqlite_error_reason(exc),
            )
            return audit_trail
        except Exception as exc:
            logger.warning(
                "Audit degraded stage=message_ignored error=%s",
                exc.__class__.__name__,
            )
            return audit_trail

    def _audit_response_sent(
        self,
        audit_trail: AuditTrail | None,
        outbound: OutboundMessage,
    ) -> AuditTrail | None:
        if self.audit_flow is None or audit_trail is None:
            return audit_trail
        try:
            return self.audit_flow.response_sent(audit_trail, outbound)
        except sqlite3.Error as exc:
            self._mark_audit_degraded(exc)
            logger.warning("Audit degraded stage=response_sent error=%s", _sqlite_error_reason(exc))
            return audit_trail
        except Exception as exc:
            logger.warning("Audit degraded stage=response_sent error=%s", exc.__class__.__name__)
            return audit_trail

    def _audit_response_failed(self, audit_trail: AuditTrail | None) -> AuditTrail | None:
        if self.audit_flow is None or audit_trail is None:
            return audit_trail
        try:
            return self.audit_flow.response_failed(audit_trail)
        except sqlite3.Error as exc:
            self._mark_audit_degraded(exc)
            logger.warning(
                "Audit degraded stage=response_failed error=%s",
                _sqlite_error_reason(exc),
            )
            return audit_trail
        except Exception as exc:
            logger.warning("Audit degraded stage=response_failed error=%s", exc.__class__.__name__)
            return audit_trail

    def _mark_audit_degraded(self, exc: sqlite3.Error) -> None:
        if self.bridge_health is not None:
            self.bridge_health.set_audit_db_health("degraded", reason=_sqlite_error_reason(exc))

    def _handle_response_send_failure(
        self,
        audit_trail: AuditTrail | None,
        exc: Exception,
    ) -> None:
        reason = _transport_error_reason(exc)
        if self.bridge_health is not None:
            self.bridge_health.record_failure(reason)
        logger.warning("Response send failed reason=%s", reason)
        self._audit_response_failed(audit_trail)


def _sqlite_error_reason(exc: sqlite3.Error) -> str:
    message = str(exc).lower()
    if "locked" in message:
        return "database_locked"
    return exc.__class__.__name__


def _transport_error_reason(exc: Exception) -> str:
    if isinstance(exc, TimeoutError | AsyncTimeoutError):
        return "transport_timeout"
    message = str(exc).lower()
    if "disconnect" in message or "closed" in message:
        return "websocket_disconnected"
    if "service" in message:
        return "transport_service_error"
    return "transport_error"


def _trim_lora_response(text: str, max_bytes: int = 180) -> str:
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(normalized.encode("utf-8")) <= max_bytes:
        return normalized
    suffix = "\n... pide detalle"
    suffix_bytes = suffix.encode("utf-8")
    if max_bytes <= len(suffix_bytes):
        return _truncate_utf8(normalized, max_bytes)
    body = _truncate_utf8(normalized, max_bytes - len(suffix_bytes)).rstrip()
    while body and len((body + suffix).encode("utf-8")) > max_bytes:
        body = body[:-1].rstrip()
    return body + suffix


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
