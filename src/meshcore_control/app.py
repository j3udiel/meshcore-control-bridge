from __future__ import annotations

import logging
from typing import Protocol

from meshcore_control.auth.roles import Role
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
    ) -> None:
        self.transport = transport
        self.router = router
        self.deduplicator = deduplicator
        self.audit_flow = audit_flow
        self.rate_limiter = rate_limiter or RateLimiter()
        self.channel_index = channel_index
        self.normal_text_forwarder = normal_text_forwarder

    async def process_message(self, message: InboundMessage) -> OutboundMessage | None:
        audit_trail = self.audit_flow.message_received(message) if self.audit_flow else None
        if message.channel_index != self.channel_index:
            logger.info("Message ignored reason=wrong_channel channel=%s", message.channel_index)
            if self.audit_flow is not None and audit_trail is not None:
                self.audit_flow.message_ignored(audit_trail, reason="wrong_channel")
            return None
        if not self.router.authorizer.allows_room(message):
            room_id = message.source_room.room_id if message.source_room is not None else "unknown"
            logger.info("Message ignored reason=room_not_allowed room=%s", room_id)
            if self.audit_flow is not None and audit_trail is not None:
                self.audit_flow.message_ignored(audit_trail, reason="room_not_allowed")
            return None
        if self.deduplicator.seen_or_store(message):
            logger.info("Message ignored reason=duplicate channel=%s", message.channel_index)
            if self.audit_flow is not None and audit_trail is not None:
                self.audit_flow.message_ignored(audit_trail, reason="duplicate")
            return None
        if not self.rate_limiter.allow(message.sender_id):
            logger.warning("Message rejected reason=rate_limited channel=%s", message.channel_index)
            if self.audit_flow is not None and audit_trail is not None:
                audit_trail = self.audit_flow.message_ignored(audit_trail, reason="rate_limited")
            outbound = OutboundMessage(
                destination=message.sender_id,
                channel_index=self.channel_index,
                text="Rate limit.",
                reply_to=message.message_id,
                reply_target=message.reply_target,
            )
            try:
                await self.transport.send(outbound)
            except Exception:
                if self.audit_flow is not None and audit_trail is not None:
                    self.audit_flow.response_failed(audit_trail)
                raise
            if self.audit_flow is not None and audit_trail is not None:
                self.audit_flow.response_sent(audit_trail, outbound)
            return outbound
        response_text = await self.router.handle(message, audit_trail=audit_trail)
        if response_text is None:
            if self.normal_text_forwarder is not None:
                if self.router.authorizer.require_message(message, Role.readonly) is None:
                    logger.info(
                        "Message ignored reason=sender_not_registered channel=%s",
                        message.channel_index,
                    )
                    if self.audit_flow is not None and audit_trail is not None:
                        self.audit_flow.message_ignored(
                            audit_trail,
                            reason="sender_not_registered",
                        )
                    return None
                handled = await self.normal_text_forwarder.forward_normal_text(
                    message,
                    audit_trail=audit_trail,
                )
                if handled:
                    return None
            if self.audit_flow is not None and audit_trail is not None:
                self.audit_flow.message_ignored(audit_trail, reason="not_a_command")
            return None
        outbound = OutboundMessage(
            destination=message.sender_id,
            channel_index=self.channel_index,
            text=_trim_lora_response(response_text),
            reply_to=message.message_id,
            reply_target=message.reply_target,
        )
        try:
            await self.transport.send(outbound)
        except Exception:
            if self.audit_flow is not None and audit_trail is not None:
                self.audit_flow.response_failed(audit_trail)
            raise
        if self.audit_flow is not None and audit_trail is not None:
            self.audit_flow.response_sent(audit_trail, outbound)
        logger.info("Response sent channel=%s", outbound.channel_index)
        return outbound

    async def run_forever(self) -> None:
        while True:
            message = await self.transport.receive()
            await self.process_message(message)

    async def close(self) -> None:
        await self.transport.close()


def _trim_lora_response(text: str, max_chars: int = 480) -> str:
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 18].rstrip() + "\n... pide detalle"
