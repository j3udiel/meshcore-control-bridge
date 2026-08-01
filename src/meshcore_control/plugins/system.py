from __future__ import annotations

from typing import Protocol, cast

from meshcore_control.adapters.homeassistant import HomeAssistantStatus
from meshcore_control.auth.roles import Role
from meshcore_control.commands.help import render_help
from meshcore_control.commands.registry import CommandContext, CommandDefinition, CommandRegistry


class HomeAssistantStatusClient(Protocol):
    async def check_available(self) -> HomeAssistantStatus:
        raise NotImplementedError


def register(registry: CommandRegistry) -> None:
    registry.register(
        CommandDefinition(
            name="ping",
            aliases=(),
            group="system",
            usage="!ping",
            help_text="Comprueba que el bridge responde.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=ping,
        )
    )
    registry.register(
        CommandDefinition(
            name="help",
            aliases=("h",),
            group="system",
            usage="!help [grupo]",
            help_text="Muestra ayuda segun permisos.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=help_command,
        )
    )
    registry.register(
        CommandDefinition(
            name="estado",
            aliases=("status",),
            group="system",
            usage="!estado",
            help_text="Resumen corto de disponibilidad.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=estado,
        )
    )


async def ping(context: CommandContext, args: list[str]) -> str:
    return "pong"


async def help_command(context: CommandContext, args: list[str]) -> str:
    registry = context.services["registry"]
    if not isinstance(registry, CommandRegistry):
        raise TypeError("registry service is not a CommandRegistry")
    group = args[0].lower() if args else None
    return render_help(registry, context.user.role, group)


async def estado(context: CommandContext, args: list[str]) -> str:
    ha_client = context.services.get("homeassistant")
    if ha_client is None:
        ha_status = HomeAssistantStatus(available=False, message="sin configurar")
    else:
        ha_status = await cast(HomeAssistantStatusClient, ha_client).check_available()

    companion_status = (
        "OK" if context.message.transport == "meshcore" else context.message.transport.upper()
    )
    ha_text = "OK" if ha_status.available else f"ERROR {ha_status.message}"
    return "\n".join(
        [
            "CASA",
            f"HA: {ha_text}",
            "Internet: no requerido",
            f"Companion: {companion_status}",
            "Servers: sin configurar",
        ]
    )
