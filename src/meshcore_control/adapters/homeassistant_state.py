from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol


class HomeAssistantStateClient(Protocol):
    async def get_state(self, entity_or_alias: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class EntityState:
    entity_id: str
    status: str
    state: str | None = None
    friendly_name: str | None = None
    unit: str | None = None
    last_changed: str | None = None

    @property
    def available(self) -> bool:
        return self.status == "ok"


class HomeAssistantStateReader:
    def __init__(
        self,
        client: HomeAssistantStateClient,
        *,
        per_entity_timeout_seconds: float = 1.5,
        total_timeout_seconds: float = 4.0,
        concurrency: int = 8,
    ) -> None:
        self._client = client
        self._per_entity_timeout_seconds = per_entity_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._semaphore = asyncio.Semaphore(concurrency)

    async def get_state(self, entity_id: str) -> EntityState:
        if not entity_id:
            return EntityState(entity_id="", status="not_configured")
        async with self._semaphore:
            try:
                raw = await asyncio.wait_for(
                    self._client.get_state(entity_id),
                    timeout=self._per_entity_timeout_seconds,
                )
            except TimeoutError:
                return EntityState(entity_id=entity_id, status="transport_error")
            except Exception:
                return EntityState(entity_id=entity_id, status="transport_error")
        return _parse_state(entity_id, raw)

    async def get_states(self, entity_ids: list[str] | tuple[str, ...]) -> dict[str, EntityState]:
        unique: list[str] = []
        seen: set[str] = set()
        for entity_id in entity_ids:
            if entity_id and entity_id not in seen:
                unique.append(entity_id)
                seen.add(entity_id)
        bulk = await self._get_states_bulk(unique)
        if bulk is not None:
            return bulk
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(self.get_state(entity_id) for entity_id in unique)),
                timeout=self._total_timeout_seconds,
            )
        except TimeoutError:
            return {
                entity_id: EntityState(entity_id=entity_id, status="transport_error")
                for entity_id in unique
            }
        return dict(zip(unique, results, strict=True))

    async def _get_states_bulk(self, entity_ids: list[str]) -> dict[str, EntityState] | None:
        method = getattr(self._client, "get_states", None)
        if not callable(method):
            return None
        try:
            raw_states = await asyncio.wait_for(method(), timeout=self._total_timeout_seconds)
        except TimeoutError:
            return {
                entity_id: EntityState(entity_id=entity_id, status="transport_error")
                for entity_id in entity_ids
            }
        except Exception:
            return {
                entity_id: EntityState(entity_id=entity_id, status="transport_error")
                for entity_id in entity_ids
            }
        if not isinstance(raw_states, list):
            return {
                entity_id: EntityState(entity_id=entity_id, status="transport_error")
                for entity_id in entity_ids
            }
        by_id = {
            str(raw.get("entity_id", "")): raw
            for raw in raw_states
            if isinstance(raw, dict) and raw.get("entity_id")
        }
        return {
            entity_id: _parse_state(entity_id, by_id[entity_id])
            if entity_id in by_id
            else EntityState(entity_id=entity_id, status="unknown")
            for entity_id in entity_ids
        }


def _parse_state(entity_id: str, raw: dict[str, Any]) -> EntityState:
    state = str(raw.get("state", "") or "")
    if state == "unavailable":
        status = "unavailable"
    elif state == "unknown" or not state:
        status = "unknown"
    else:
        status = "ok"
    attributes = raw.get("attributes", {})
    friendly_name = None
    unit = None
    if isinstance(attributes, dict):
        raw_name = attributes.get("friendly_name")
        if isinstance(raw_name, str):
            friendly_name = _safe_label(raw_name)
        raw_unit = attributes.get("unit_of_measurement")
        if isinstance(raw_unit, str) and _is_safe_short(raw_unit):
            unit = raw_unit
    raw_last_changed = raw.get("last_changed")
    last_changed = raw_last_changed if isinstance(raw_last_changed, str) else None
    return EntityState(
        entity_id=entity_id,
        status=status,
        state=state,
        friendly_name=friendly_name,
        unit=unit,
        last_changed=last_changed,
    )


def _safe_label(value: str) -> str | None:
    label = " ".join(value.strip().split())
    if not label:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in label):
        return None
    return label[:32]


def _is_safe_short(value: str) -> bool:
    return bool(value) and len(value) <= 12 and not any(
        ord(character) < 32 or ord(character) == 127 for character in value
    )
