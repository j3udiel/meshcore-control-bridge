from __future__ import annotations

import time

from meshcore_control.auth.authorization import Authorizer
from meshcore_control.commands.parser import parse_command
from meshcore_control.commands.registry import CommandContext, CommandRegistry
from meshcore_control.models import InboundMessage
from meshcore_control.storage.repositories import AuditRepository


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
                return "Comando desconocido. Usa !help"

            user = self.authorizer.require(message.sender_id, definition.minimum_role)
            if user is None:
                result = "unauthorized"
                return "No autorizado."

            context = CommandContext(message=message, user=user, services=self.services)
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
