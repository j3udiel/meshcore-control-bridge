from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from meshcore_control.adapters.homeassistant_state import EntityState, HomeAssistantStateReader
from meshcore_control.bridge_health import BridgeHealthState, relative_time
from meshcore_control.commands.registry import CommandContext
from meshcore_control.config import AppConfig

_LORA_LIMIT = 480
_ALARM_LABELS = {
    "disarmed": ("desarmada", "dis"),
    "armed_home": ("armada en casa", "home"),
    "armed_away": ("armada fuera", "away"),
    "armed_night": ("armada noche", "night"),
    "armed_vacation": ("armada vacaciones", "vac"),
    "armed_custom_bypass": ("armada parcial", "part"),
    "arming": ("armandose", "arming"),
    "disarming": ("desarmandose", "disarm"),
    "pending": ("pendiente", "pend"),
    "triggered": ("ALERTA ACTIVADA", "TRIGGERED"),
    "unavailable": ("no disponible", "N/D"),
    "unknown": ("N/D", "N/D"),
}


@dataclass(frozen=True, slots=True)
class HomeStatusServices:
    config: AppConfig
    reader: HomeAssistantStateReader | None
    health: BridgeHealthState | None
    compact: bool


def services_from_context(context: CommandContext) -> HomeStatusServices:
    config = context.services.get("config")
    if not isinstance(config, AppConfig):
        raise RuntimeError("config service unavailable")
    reader = context.services.get("home_status_reader")
    health = context.services.get("bridge_health")
    return HomeStatusServices(
        config=config,
        reader=reader if isinstance(reader, HomeAssistantStateReader) else None,
        health=health if isinstance(health, BridgeHealthState) else None,
        compact=context.message.transport != "telegram",
    )


def set_audit_counts(
    context: CommandContext,
    *,
    queried: int,
    available: int,
    failure_reason: str = "none",
) -> None:
    context.audit_metadata["entities_queried"] = queried
    context.audit_metadata["entities_available"] = available
    context.audit_metadata["safe_failure_reason"] = failure_reason


async def read_configured(
    reader: HomeAssistantStateReader | None,
    entity_ids: Iterable[str],
) -> dict[str, EntityState]:
    ids = [entity_id for entity_id in entity_ids if entity_id]
    if reader is None or not ids:
        return {}
    return await reader.get_states(tuple(ids))


def available_count(states: Iterable[EntityState]) -> int:
    return sum(1 for state in states if state.available)


def failure_reason(states: Iterable[EntityState], *, configured: bool) -> str:
    state_list = list(states)
    if not configured:
        return "not_configured"
    if any(state.status == "transport_error" for state in state_list):
        return "transport_error"
    if state_list and not any(state.available for state in state_list):
        return "unavailable"
    return "none"


def alarm_label(state: EntityState | None, *, compact: bool = False) -> str:
    if state is None:
        return "N/D"
    value = state.state if state.status == "ok" else state.status
    long_label, short_label = _ALARM_LABELS.get(value or "unknown", ("N/D", "N/D"))
    return short_label if compact else long_label


def binary_online(state: EntityState | None) -> str:
    if state is None:
        return "N/D"
    if state.status == "unavailable":
        return "unavailable"
    if state.status != "ok":
        return "unknown"
    return "online" if state.state == "on" else "offline" if state.state == "off" else "unknown"


def binary_on_off(state: EntityState | None, *, on: str = "on", off: str = "off") -> str:
    if state is None:
        return "N/D"
    if state.status != "ok":
        return "N/D"
    if state.state == "on":
        return on
    if state.state == "off":
        return off
    return "N/D"


def count_on(states: Iterable[EntityState]) -> int:
    return sum(1 for state in states if state.available and state.state == "on")


def open_names(states: Iterable[EntityState]) -> list[str]:
    names: list[str] = []
    for index, state in enumerate(states, start=1):
        if state.available and state.state == "on":
            names.append(state.friendly_name or f"Puerta {index}")
    return names


def motion_detected(states: Iterable[EntityState]) -> list[str]:
    names: list[str] = []
    for index, state in enumerate(states, start=1):
        if state.available and state.state == "on":
            names.append(state.friendly_name or f"Sensor {index}")
    return names


def format_measurement(state: EntityState | None, *, compact_unit: bool = False) -> str:
    if state is None or not state.available or state.state is None:
        return "N/D"
    unit = state.unit or ""
    value = state.state
    if compact_unit:
        unit = unit.replace("°", "").replace(" ", "")
        return f"{value}{unit}"
    return f"{value} {unit}".strip()


def format_percent(state: EntityState | None, *, compact: bool = False) -> str:
    value = format_measurement(state, compact_unit=compact)
    if value == "N/D":
        return value
    if "%" not in value:
        return f"{value}%"
    return value


def last_changed(state: EntityState | None, *, compact: bool = False) -> str | None:
    if state is None or not state.last_changed:
        return None
    try:
        changed = datetime.fromisoformat(state.last_changed.replace("Z", "+00:00"))
    except ValueError:
        return None
    rendered = relative_time(changed.astimezone(UTC))
    if rendered == "never":
        return None
    return rendered if compact else _with_ago_es(rendered)


def health_relative(value: datetime | None, *, compact: bool) -> str:
    rendered = relative_time(value)
    if compact:
        return rendered
    return _with_ago_es(rendered)


def fit_lora(text: str, max_chars: int = _LORA_LIMIT) -> str:
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(normalized) <= max_chars:
        return normalized
    marker = "\n..."
    return normalized[: max_chars - len(marker)].rstrip() + marker


def _with_ago_es(value: str) -> str:
    if value == "never":
        return "nunca"
    if value == "now":
        return "ahora"
    return f"hace {value}"
