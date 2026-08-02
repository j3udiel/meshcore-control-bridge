from __future__ import annotations

from meshcore_control.auth.roles import Role
from meshcore_control.commands.registry import CommandRegistry


def render_help(registry: CommandRegistry, role: Role, group: str | None = None) -> str:
    commands = registry.group_commands(group, role) if group else registry.visible_commands(role)
    if not commands:
        return "Sin comandos disponibles."

    if group:
        lines = [group.upper()]
        lines.extend(definition.usage for definition in commands)
        return "\n".join(lines[:8])

    lines = ["MeshCore Bridge"]
    lines.extend(definition.usage for definition in commands[:6])
    groups = sorted({definition.group for definition in commands})
    if groups:
        lines.append("Mas: !help <grupo>")
    return "\n".join(lines)
