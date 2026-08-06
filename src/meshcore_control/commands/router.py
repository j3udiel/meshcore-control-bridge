from __future__ import annotations

import logging
import sqlite3
import time

from meshcore_control.auth.authorization import Authorizer
from meshcore_control.auth.roles import Role
from meshcore_control.bridge_health import BridgeHealthState
from meshcore_control.commands.parser import ParsedCommand, parse_command
from meshcore_control.commands.registry import CommandContext, CommandRegistry
from meshcore_control.models import InboundMessage
from meshcore_control.storage.audit_flow import AuditFlow, AuditTrail
from meshcore_control.storage.repositories import AuditRepository

logger = logging.getLogger(__name__)


class CommandRouter:
    def __init__(
        self,
        *,
        registry: CommandRegistry,
        authorizer: Authorizer,
        audit: AuditRepository,
        audit_flow: AuditFlow | None = None,
        services: dict[str, object],
        prefix: str,
    ) -> None:
        self.registry = registry
        self.authorizer = authorizer
        self.audit = audit
        self.audit_flow = audit_flow
        self.services = services
        self.prefix = prefix

    async def handle(
        self,
        message: InboundMessage,
        audit_trail: AuditTrail | None = None,
    ) -> str | None:
        parsed = parse_command(message.text, prefix=self.prefix)
        if self.audit_flow is not None and audit_trail is not None:
            audit_trail = self._audit_command_parsed(audit_trail, parsed=parsed)
        if parsed is None:
            return None

        started = time.monotonic()
        command_name = parsed.name
        result = "ignored"
        error: str | None = None
        registered_command = False
        command_audit_metadata: dict[str, object] = {}
        try:
            definition = self.registry.resolve(command_name)
            if definition is None:
                result = "unknown"
                logger.info(
                    "Command rejected command=%s authorization=denied reason=unknown_command",
                    command_name,
                )
                return "Comando desconocido. Usa !help"
            registered_command = True

            user = self.authorizer.require_message(message, definition.minimum_role)
            if user is None:
                result = "unauthorized"
                sender_id = (
                    message.sender.sender_id if message.sender is not None else message.sender_id
                )
                existing_user = self.authorizer.get_user(sender_id)
                reason = _authorization_denial_reason(
                    existing_user,
                    definition.minimum_role,
                    self.authorizer.allows_room(message),
                )
                if self.audit_flow is not None and audit_trail is not None:
                    audit_trail = self._audit_command_authorization(
                        audit_trail,
                        result="denied",
                        reason=reason,
                        command_name=definition.name,
                    )
                logger.info(
                    "Command rejected command=%s authorization=denied reason=%s",
                    definition.name,
                    reason,
                )
                return "No autorizado."

            context = CommandContext(
                message=message,
                user=user,
                services=self.services,
                audit_trail=audit_trail,
            )
            if self.audit_flow is not None and audit_trail is not None:
                audit_trail = self._audit_command_authorization(
                    audit_trail,
                    result="allowed",
                    reason="allowed",
                    command_name=definition.name,
                )
            logger.info("Command accepted command=%s authorization=allowed", definition.name)
            result_text = await definition.handler(context, parsed.args)
            command_audit_metadata = dict(context.audit_metadata)
            result = "succeeded"
            health = self.services.get("bridge_health")
            if isinstance(health, BridgeHealthState):
                health.record_command_processed()
            return result_text
        except Exception as exc:
            result = "failed"
            error = exc.__class__.__name__
            return f"ERROR {exc}"
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            if self.audit_flow is not None and audit_trail is not None:
                self._audit_command_execution(
                    audit_trail,
                    command=command_name,
                    args=parsed.args,
                    result=result,
                    duration_ms=duration_ms,
                    error=error,
                    registered_command=registered_command,
                    extra_metadata=command_audit_metadata,
                )
            else:
                self._audit_legacy_command(
                    message=message,
                    command=command_name,
                    args=parsed.args,
                    result=result,
                    duration_ms=duration_ms,
                    error=error,
                )

    def _audit_command_parsed(
        self,
        audit_trail: AuditTrail,
        *,
        parsed: ParsedCommand | None,
    ) -> AuditTrail:
        try:
            if self.audit_flow is None:
                return audit_trail
            return self.audit_flow.command_parsed(
                audit_trail,
                parsed=parsed,
                registry=self.registry,
            )
        except sqlite3.Error as exc:
            logger.warning(
                "Audit degraded stage=command_parsed error=%s",
                _sqlite_error_reason(exc),
            )
            return audit_trail
        except Exception as exc:
            logger.warning("Audit degraded stage=command_parsed error=%s", exc.__class__.__name__)
            return audit_trail

    def _audit_command_authorization(
        self,
        audit_trail: AuditTrail,
        *,
        result: str,
        reason: str,
        command_name: str | None,
    ) -> AuditTrail:
        try:
            if self.audit_flow is None:
                return audit_trail
            return self.audit_flow.command_authorization(
                audit_trail,
                result=result,
                reason=reason,
                command_name=command_name,
            )
        except sqlite3.Error as exc:
            logger.warning(
                "Audit degraded stage=command_authorization error=%s",
                _sqlite_error_reason(exc),
            )
            return audit_trail
        except Exception as exc:
            logger.warning(
                "Audit degraded stage=command_authorization error=%s",
                exc.__class__.__name__,
            )
            return audit_trail

    def _audit_command_execution(
        self,
        audit_trail: AuditTrail,
        *,
        command: str,
        args: list[str],
        result: str,
        duration_ms: int,
        error: str | None,
        registered_command: bool,
        extra_metadata: dict[str, object] | None = None,
    ) -> AuditTrail:
        try:
            if self.audit_flow is None:
                return audit_trail
            return self.audit_flow.command_execution(
                audit_trail,
                command=command,
                args=args,
                result=result,
                duration_ms=duration_ms,
                error=error,
                registered_command=registered_command,
                extra_metadata=extra_metadata,
            )
        except sqlite3.Error as exc:
            logger.warning(
                "Audit degraded stage=command_execution error=%s",
                _sqlite_error_reason(exc),
            )
            return audit_trail
        except Exception as exc:
            logger.warning(
                "Audit degraded stage=command_execution error=%s",
                exc.__class__.__name__,
            )
            return audit_trail

    def _audit_legacy_command(
        self,
        *,
        message: InboundMessage,
        command: str,
        args: list[str],
        result: str,
        duration_ms: int,
        error: str | None,
    ) -> None:
        try:
            self.audit.record_inbound_message(message)
            self.audit.record_command(
                message=message,
                command=command,
                args=args,
                result=result,
                duration_ms=duration_ms,
                error=error,
            )
        except sqlite3.Error as exc:
            logger.warning(
                "Audit degraded stage=legacy_command error=%s",
                _sqlite_error_reason(exc),
            )
        except Exception as exc:
            logger.warning("Audit degraded stage=legacy_command error=%s", exc.__class__.__name__)


def _authorization_denial_reason(
    user: object | None,
    minimum_role: Role,
    room_allowed: bool,
) -> str:
    if not room_allowed:
        return "room_not_allowed"
    if user is None:
        return "sender_not_registered"
    if minimum_role > Role.readonly:
        return "command_not_readonly"
    return "insufficient_role"


def _sqlite_error_reason(exc: sqlite3.Error) -> str:
    message = str(exc).lower()
    if "locked" in message:
        return "database_locked"
    return exc.__class__.__name__
