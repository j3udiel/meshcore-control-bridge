from __future__ import annotations

from typing import Any, Protocol, cast

from meshcore_control.adapters.homeassistant import HomeAssistantStatus
from meshcore_control.auth.roles import Role
from meshcore_control.commands.help import render_help
from meshcore_control.commands.registry import CommandContext, CommandDefinition, CommandRegistry
from meshcore_control.config import AppConfig


class HomeAssistantStatusClient(Protocol):
    async def check_available(self) -> HomeAssistantStatus:
        raise NotImplementedError

    async def get_config(self) -> dict[str, Any]:
        raise NotImplementedError

    async def get_state(self, entity_or_alias: str) -> dict[str, Any]:
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
    config = context.services.get("config")
    if ha_client is None:
        ha_status = HomeAssistantStatus(available=False, message="sin configurar")
    else:
        ha_status = await cast(HomeAssistantStatusClient, ha_client).check_available()

    if args and args[0].lower() == "ha":
        return await _render_ha_status(ha_client, ha_status)

    companion_status = (
        "OK"
        if context.message.transport.startswith("meshcore")
        else context.message.transport.upper()
    )
    ha_text = "OK" if ha_status.available else f"ERROR {ha_status.message}"
    lines = ["CASA", f"HA: {ha_text}"]
    if isinstance(config, AppConfig) and ha_client is not None and ha_status.available:
        lines.extend(await _status_entity_lines(cast(HomeAssistantStatusClient, ha_client), config))
    lines.extend(
        [
            "Internet: no requerido",
            f"Companion: {companion_status}",
            "Servers: sin configurar",
        ]
    )
    return "\n".join(lines)


async def _render_ha_status(ha_client: object | None, status: HomeAssistantStatus) -> str:
    if ha_client is None:
        return "HA: sin configurar"
    if not status.available:
        return f"HA: ERROR {status.message}"
    try:
        ha_config = await cast(HomeAssistantStatusClient, ha_client).get_config()
    except Exception as exc:
        return f"HA: OK\nConfig: ERROR {exc.__class__.__name__}"
    version = ha_config.get("version", "N/D")
    location = ha_config.get("location_name", "N/D")
    return f"HA: OK\nVersion: {version}\nName: {location}"


async def _status_entity_lines(
    ha_client: HomeAssistantStatusClient,
    config: AppConfig,
) -> list[str]:
    lines: list[str] = []
    for entity in config.status_entities.values():
        try:
            state = await ha_client.get_state(entity.entity_id)
        except Exception:
            lines.append(f"{entity.label}: N/D")
            continue
        value = _format_state(state)
        lines.append(f"{entity.label}: {value}")
    return lines


def _format_state(state: dict[str, Any]) -> str:
    value = str(state.get("state", "N/D"))
    attributes = state.get("attributes", {})
    unit = ""
    if isinstance(attributes, dict):
        unit_value = attributes.get("unit_of_measurement")
        unit = f" {unit_value}" if unit_value else ""
    return f"{value}{unit}"
