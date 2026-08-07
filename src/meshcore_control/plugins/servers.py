from __future__ import annotations

from meshcore_control.auth.roles import Role
from meshcore_control.commands.registry import CommandContext, CommandDefinition, CommandRegistry
from meshcore_control.config import HomeStatusServerEntryConfig
from meshcore_control.plugins.home_status import (
    available_count,
    binary_online,
    failure_reason,
    fit_lora,
    format_measurement,
    format_percent,
    read_configured,
    services_from_context,
    set_audit_counts,
)

_MAX_COMPACT_SERVERS = 8


def register(registry: CommandRegistry) -> None:
    registry.register(
        CommandDefinition(
            name="servers",
            aliases=(),
            group="servers",
            usage="!servers\n!servers <alias>",
            help_text="Resumen o detalle readonly de servidores configurados.",
            minimum_role=Role.readonly,
            confirmation_required=False,
            handler=servers,
        )
    )


async def servers(context: CommandContext, args: list[str]) -> str:
    services = services_from_context(context)
    entries = services.config.home_status.servers.entries
    if not entries:
        set_audit_counts(context, queried=0, available=0, failure_reason="not_configured")
        return "Srv:N/D" if services.compact else "Servidores: no configurados."
    alias = args[0].lower() if args else None
    if alias:
        entry = next((item for item in entries if item.alias == alias), None)
        if entry is None:
            set_audit_counts(context, queried=0, available=0, failure_reason="not_configured")
            return "Servidor no configurado."
        return await _server_detail(context, entry)
    return await _servers_summary(context, list(entries))


async def _servers_summary(
    context: CommandContext,
    entries: list[HomeStatusServerEntryConfig],
) -> str:
    services = services_from_context(context)
    entity_ids = [entry.availability_entity for entry in entries if entry.availability_entity]
    states = await read_configured(services.reader, entity_ids)
    set_audit_counts(
        context,
        queried=len(entity_ids),
        available=available_count(states.values()),
        failure_reason=failure_reason(states.values(), configured=bool(entity_ids)),
    )
    online = 0
    rendered: list[tuple[str, str]] = []
    for entry in entries:
        state = states.get(entry.availability_entity)
        status = binary_online(state)
        if status == "online":
            online += 1
        rendered.append((entry.alias if services.compact else entry.name, status))
    if services.compact:
        visible = rendered[:_MAX_COMPACT_SERVERS]
        lines = [f"Srv {online}/{len(entries)}"]
        lines.extend(f"{alias}:{_compact_status(status)}" for alias, status in visible)
        hidden = len(rendered) - len(visible)
        if hidden > 0:
            lines.append(f"+{hidden} mas")
        return fit_lora("\n".join(lines), max_bytes=services.meshcore_max_bytes)
    lines = [f"{name}: {status}" for name, status in rendered]
    lines.append(f"Total: {online}/{len(entries)} online")
    return "\n".join(lines)


async def _server_detail(
    context: CommandContext,
    entry: HomeStatusServerEntryConfig,
) -> str:
    services = services_from_context(context)
    entity_ids = [
        entry.availability_entity,
        entry.cpu_entity,
        entry.memory_entity,
        entry.disk_entity,
        entry.temperature_entity,
        entry.health_entity,
    ]
    states = await read_configured(services.reader, entity_ids)
    set_audit_counts(
        context,
        queried=len([entity_id for entity_id in entity_ids if entity_id]),
        available=available_count(states.values()),
        failure_reason=failure_reason(states.values(), configured=True),
    )
    availability = states.get(entry.availability_entity)
    cpu = states.get(entry.cpu_entity)
    memory = states.get(entry.memory_entity)
    disk = states.get(entry.disk_entity)
    temperature = states.get(entry.temperature_entity)
    health = states.get(entry.health_entity)
    status = binary_online(availability)
    if services.compact:
        lines = [f"{entry.alias}:{_compact_status(status)}"]
        metrics = []
        if cpu is not None:
            metrics.append(f"CPU:{format_percent(cpu, compact=True).replace('%', '')}")
        if memory is not None:
            metrics.append(f"RAM:{format_percent(memory, compact=True).replace('%', '')}")
        if metrics:
            lines.append(" ".join(metrics))
        metrics = []
        if disk is not None:
            metrics.append(f"Disk:{format_percent(disk, compact=True).replace('%', '')}")
        if temperature is not None:
            metrics.append(f"T:{format_measurement(temperature, compact_unit=True)}")
        if metrics:
            lines.append(" ".join(metrics))
        return fit_lora("\n".join(lines), max_bytes=services.meshcore_max_bytes)
    lines = [entry.name, f"Estado: {status}"]
    if cpu is not None:
        lines.append(f"CPU: {format_percent(cpu)}")
    if memory is not None:
        lines.append(f"RAM: {format_percent(memory)}")
    if disk is not None:
        lines.append(f"Disco: {format_percent(disk)}")
    if temperature is not None:
        lines.append(f"Temperatura: {format_measurement(temperature)}")
    if health is not None and health.available and health.state:
        lines.append(f"Salud: {health.state}")
    return "\n".join(lines)


def _compact_status(status: str) -> str:
    if status == "online":
        return "on"
    if status == "offline":
        return "off"
    if status == "unavailable":
        return "unav"
    return "unk"
