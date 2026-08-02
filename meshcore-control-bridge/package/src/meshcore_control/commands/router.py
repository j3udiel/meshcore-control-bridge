from __future__ import annotations

import logging
import time

from meshcore_control.auth.authorization import Authorizer
from meshcore_control.auth.roles import Role
from meshcore_control.commands.parser import parse_command
from meshcore_control.commands.registry import CommandContext, CommandRegistry
from meshcore_control.models import InboundMessage
from meshcore_control.storage.repositories import AuditRepository

logger = logging.getLogger(__name__)


class CommandRouter:
    def __init__(
        self,
        *,
        registry: CommandRegistry,
        authorizer: Authorizer,
        audit: AuditRepository,
        services: dict[str, object],
        prefix: str,
    ) -> None:
        self.registry = registry
        self.authorizer = authorizer
        self.audit = audit
        self.services = services
        self.prefix = prefix

    async def handle(self, message: InboundMessage) -> str | None:
        parsed = parse_command(message.text, prefix=self.prefix)
        if parsed is None:
            return None

        started = time.monotonic()
        command_name = parsed.name
        result = "ignored"
        error: str | None = None
        self.audit.record_inbound_message(message)
        try:
            definition = self.registry.resolve(command_name)
            if definition is None:
                result = "unknown"
                logger.info(
                    "Command rejected command=%s authorization=denied reason=unknown_command",
                    command_name,
                )
                return "Comando desconocido. Usa !help"

            user = self.authorizer.require(message.sender_id, definition.minimum_role)
            if user is None:
                result = "unauthorized"
                existing_user = self.authorizer.get_user(message.sender_id)
                logger.info(
                    "Command rejected command=%s authorization=denied reason=%s",
                    definition.name,
                    _authorization_denial_reason(existing_user, definition.minimum_role),
                )
                return "No autorizado."

            context = CommandContext(message=message, user=user, services=self.services)
            logger.info("Command accepted command=%s authorization=allowed", definition.name)
            result_text = await definition.handler(context, parsed.args)
            result = "succeeded"
            return result_text
        except Exception as exc:
            result = "failed"
            error = exc.__class__.__name__
            return f"ERROR {exc}"
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            self.audit.record_command(
                message=message,
                command=command_name,
                args=parsed.args,
                result=result,
                duration_ms=duration_ms,
                error=error,
            )


def _authorization_denial_reason(user: object | None, minimum_role: Role) -> str:
    if user is None:
        return "sender_not_registered"
    if minimum_role > Role.readonly:
        return "command_not_readonly"
    return "insufficient_role"
