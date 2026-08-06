from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from meshcore_control.auth.authorization import AuthorizedUser
from meshcore_control.auth.roles import Role
from meshcore_control.models import InboundMessage

if TYPE_CHECKING:
    from meshcore_control.storage.audit_flow import AuditTrail


@dataclass(slots=True)
class CommandContext:
    message: InboundMessage
    user: AuthorizedUser
    services: dict[str, object]
    audit_trail: AuditTrail | None = None
    audit_metadata: dict[str, object] = field(default_factory=dict)


CommandHandler = Callable[[CommandContext, list[str]], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    aliases: tuple[str, ...]
    group: str
    usage: str
    help_text: str
    minimum_role: Role
    confirmation_required: bool
    handler: CommandHandler

    @property
    def command_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandDefinition] = {}

    def register(self, definition: CommandDefinition) -> None:
        for name in definition.command_names:
            key = name.lower()
            if key in self._commands:
                raise ValueError(f"command {name!r} already registered")
            self._commands[key] = definition

    def resolve(self, name: str) -> CommandDefinition | None:
        return self._commands.get(name.lower())

    def visible_commands(self, role: Role) -> list[CommandDefinition]:
        unique: dict[str, CommandDefinition] = {}
        for definition in self._commands.values():
            if role >= definition.minimum_role:
                unique[definition.name] = definition
        return sorted(unique.values(), key=_command_sort_key)

    def group_commands(self, group: str, role: Role) -> list[CommandDefinition]:
        return [
            definition
            for definition in self.visible_commands(role)
            if definition.group == group.lower() or definition.name == group.lower()
        ]


def _command_sort_key(item: CommandDefinition) -> tuple[int, str, str]:
    return (0 if item.group == "system" else 1, item.group, item.name)
