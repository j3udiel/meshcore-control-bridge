from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from meshcore_control.commands.parser import ParsedCommand
from meshcore_control.commands.registry import CommandRegistry
from meshcore_control.models import InboundMessage, OutboundMessage
from meshcore_control.storage.database import write_transaction
from meshcore_control.storage.normalized_audit import (
    NormalizedAuditEvent,
    NormalizedAuditEventType,
    NormalizedAuditRepository,
    NormalizedAuditSettings,
)
from meshcore_control.storage.repositories import AuditRepository

logger = logging.getLogger(__name__)

IGNORE_REASONS = frozenset(
    {
        "wrong_channel",
        "room_not_allowed",
        "duplicate",
        "rate_limited",
        "not_a_command",
        "sender_not_registered",
    }
)
AUTHORIZATION_RESULTS = frozenset({"allowed", "denied"})
AUTHORIZATION_REASONS = frozenset(
    {
        "allowed",
        "sender_not_registered",
        "room_not_allowed",
        "command_not_readonly",
        "insufficient_role",
    }
)
COMMAND_RESULTS = frozenset({"ignored", "unknown", "unauthorized", "succeeded", "failed"})


@dataclass(slots=True)
class AuditTrail:
    message: InboundMessage
    received_event_id: str | None
    latest_event_id: str | None
    correlation_id: str

    def child(self, event_id: str | None) -> AuditTrail:
        if event_id is not None:
            self.latest_event_id = event_id
        return self


class AuditFlow:
    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        legacy: AuditRepository,
        normalized: NormalizedAuditRepository,
    ) -> None:
        self.connection = connection
        self.legacy = legacy
        self.normalized = normalized

    @property
    def normalized_enabled(self) -> bool:
        return self.normalized.enabled

    @classmethod
    def disabled(cls, connection: sqlite3.Connection, legacy: AuditRepository) -> AuditFlow:
        return cls(
            connection=connection,
            legacy=legacy,
            normalized=NormalizedAuditRepository(
                connection,
                NormalizedAuditSettings.legacy_disabled(),
            ),
        )

    def message_received(self, message: InboundMessage) -> AuditTrail:
        correlation_id = _message_correlation_id(message)
        if not self.normalized_enabled:
            self.normalized.warn_if_disabled()
            return AuditTrail(
                message=message,
                received_event_id=None,
                latest_event_id=None,
                correlation_id=correlation_id,
            )
        event = self.normalized.event_from_inbound(
            event_type=NormalizedAuditEventType.MESSAGE_RECEIVED,
            message=message,
            correlation_id=correlation_id,
            metadata={
                "channel_index": message.channel_index,
                "message_id_present": bool(
                    message.message.message_id if message.message else message.message_id
                ),
                "identity_kind": (
                    message.sender.identity_kind if message.sender is not None else "unknown"
                ),
                "identity_stable": message.sender.stable if message.sender is not None else False,
            },
        )
        write_transaction(self.connection, lambda: self.normalized.insert_event(event))
        return AuditTrail(
            message=message,
            received_event_id=event.event_id,
            latest_event_id=event.event_id,
            correlation_id=correlation_id,
        )

    def degraded_trail(self, message: InboundMessage) -> AuditTrail:
        return AuditTrail(
            message=message,
            received_event_id=None,
            latest_event_id=None,
            correlation_id=_message_correlation_id(message),
        )

    def message_ignored(self, trail: AuditTrail, *, reason: str) -> AuditTrail:
        if reason not in IGNORE_REASONS:
            raise ValueError("invalid audit ignore reason")
        if not self.normalized_enabled:
            return trail
        event = self.normalized.event_from_inbound(
            event_type=NormalizedAuditEventType.MESSAGE_IGNORED,
            message=trail.message,
            correlation_id=trail.correlation_id,
            metadata={"ignore_reason": reason, "channel_index": trail.message.channel_index},
            causation_event_id=trail.received_event_id,
        )
        return self._record_normalized_only(trail, event)

    def command_parsed(
        self,
        trail: AuditTrail,
        *,
        parsed: ParsedCommand | None,
        registry: CommandRegistry,
    ) -> AuditTrail:
        parse_result = _parse_result(parsed, registry)
        metadata: dict[str, object] = {"parse_result": parse_result}
        if parsed is not None:
            metadata["argument_count"] = len(parsed.args)
        command_name = parsed.name if parsed is not None and registry.resolve(parsed.name) else None
        if not self.normalized_enabled:
            return trail
        event = self._event(
            trail,
            NormalizedAuditEventType.COMMAND_PARSED,
            metadata=metadata,
            command_name=command_name,
        )
        return self._record_normalized_only(trail, event)

    def command_authorization(
        self,
        trail: AuditTrail,
        *,
        result: str,
        reason: str,
        command_name: str | None,
    ) -> AuditTrail:
        if result not in AUTHORIZATION_RESULTS:
            raise ValueError("invalid authorization result")
        if reason not in AUTHORIZATION_REASONS:
            raise ValueError("invalid authorization reason")
        if not self.normalized_enabled:
            return trail
        event = self._event(
            trail,
            NormalizedAuditEventType.COMMAND_AUTHORIZATION,
            metadata={"authorization_result": result, "authorization_reason": reason},
            command_name=command_name,
        )
        return self._record_normalized_only(trail, event)

    def command_execution(
        self,
        trail: AuditTrail,
        *,
        command: str,
        args: list[str],
        result: str,
        duration_ms: int,
        error: str | None,
        registered_command: bool,
    ) -> AuditTrail:
        if result not in COMMAND_RESULTS:
            raise ValueError("invalid command result")
        if not self.normalized_enabled:

            def record_legacy() -> None:
                self.legacy.insert_inbound_message(trail.message)
                self.legacy.insert_command(
                    message=trail.message,
                    command=command,
                    args=args,
                    result=result,
                    duration_ms=duration_ms,
                    error=error,
                )

            write_transaction(self.connection, record_legacy)
            return trail
        event = self._event(
            trail,
            NormalizedAuditEventType.COMMAND_EXECUTION,
            command_name=command if registered_command else None,
            command_result=result,
            duration_ms=duration_ms,
            metadata={"command_result": result},
        )

        def record_command() -> None:
            self.legacy.insert_inbound_message(trail.message)
            self.legacy.insert_command(
                message=trail.message,
                command=command,
                args=args,
                result=result,
                duration_ms=duration_ms,
                error=error,
            )
            self.normalized.insert_event(event)

        write_transaction(self.connection, record_command)
        return trail.child(event.event_id)

    def response_sent(self, trail: AuditTrail, outbound: OutboundMessage) -> AuditTrail:
        if not self.normalized_enabled:
            return trail
        event = self._event(
            trail,
            NormalizedAuditEventType.RESPONSE_SENT,
            metadata={
                "transport_service": "transport.send",
                "response_length": len(outbound.text),
            },
        )
        return self._record_normalized_only(trail, event)

    def response_failed(self, trail: AuditTrail) -> AuditTrail:
        if not self.normalized_enabled:
            return trail
        event = self._event(
            trail,
            NormalizedAuditEventType.RESPONSE_FAILED,
            metadata={"transport_service": "transport.send"},
        )
        return self._record_normalized_only(trail, event)

    def runtime_override_event(
        self,
        trail: AuditTrail,
        *,
        event_type: NormalizedAuditEventType,
        metadata: dict[str, object],
    ) -> AuditTrail:
        allowed_types = {
            NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_REQUESTED,
            NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_APPLIED,
            NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_DENIED,
            NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_RESET,
        }
        if event_type not in allowed_types:
            raise ValueError("invalid runtime override audit event")
        if not self.normalized_enabled:
            return trail
        event = self._event(trail, event_type, metadata=metadata)
        return self._record_normalized_only(trail, event)

    def _event(
        self,
        trail: AuditTrail,
        event_type: NormalizedAuditEventType,
        *,
        metadata: dict[str, object] | None = None,
        command_name: str | None = None,
        command_result: str | None = None,
        duration_ms: int | None = None,
    ) -> NormalizedAuditEvent:
        return self.normalized.event_from_inbound(
            event_type=event_type,
            message=trail.message,
            correlation_id=trail.correlation_id,
            metadata=metadata,
            command_name=command_name,
            command_result=command_result,
            duration_ms=duration_ms,
            causation_event_id=trail.latest_event_id,
        )

    def _record_normalized_only(
        self,
        trail: AuditTrail,
        event: NormalizedAuditEvent,
    ) -> AuditTrail:
        if not self.normalized_enabled:
            return trail
        write_transaction(self.connection, lambda: self.normalized.insert_event(event))
        return trail.child(event.event_id)


def _message_correlation_id(message: InboundMessage) -> str:
    if message.message is None:
        raise ValueError("normalized audit flow requires MessageIdentity")
    return message.message.correlation_id


def _parse_result(parsed: ParsedCommand | None, registry: CommandRegistry) -> str:
    if parsed is None:
        return "not_a_command"
    if not parsed.name:
        return "malformed"
    if registry.resolve(parsed.name) is None:
        return "unknown"
    return "recognized"
