from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meshcore_control.adapters.homeassistant_ws import (
    HomeAssistantEvent,
    HomeAssistantWebSocketClient,
)
from meshcore_control.models import InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)

MESHCORE_MESSAGE_EVENT = "meshcore_message"
MESHCORE_SEND_CHANNEL_SERVICE = "send_channel_message"


@dataclass(frozen=True, slots=True)
class HomeAssistantMeshCoreSettings:
    channel_index: int
    ha_base_url: str
    ha_token: str
    ha_verify_tls: bool = True
    ha_timeout_seconds: float = 10.0
    ha_websocket_url: str | None = None
    ha_entry_id: str | None = None
    event_types: tuple[str, ...] = (MESHCORE_MESSAGE_EVENT,)
    require_stable_sender: bool = True
    allow_channel_without_sender: bool = False
    max_message_bytes: int = 262_144
    healthcheck_path: str | None = None


class HomeAssistantMeshCoreTransport:
    name = "homeassistant-meshcore"

    def __init__(
        self,
        *,
        settings: HomeAssistantMeshCoreSettings,
        websocket_client: HomeAssistantWebSocketClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = websocket_client or HomeAssistantWebSocketClient(
            base_url=settings.ha_base_url,
            token=settings.ha_token,
            verify_tls=settings.ha_verify_tls,
            timeout_seconds=settings.ha_timeout_seconds,
            max_message_bytes=settings.max_message_bytes,
            websocket_url_override=settings.ha_websocket_url,
        )
        self._event_iterator: Any | None = None
        self._resolved_entry_id: str | None = settings.ha_entry_id
        logger.info("Listening for MeshCore messages on channel %s", settings.channel_index)
        if settings.allow_channel_without_sender:
            logger.warning("Unidentified readonly testing is enabled")
        if self._resolved_entry_id:
            logger.info("MeshCore entry selected: %s", _redact_identifier(self._resolved_entry_id))
        else:
            logger.info("MeshCore entry selection pending")
        if settings.healthcheck_path:
            self.client.on_subscribed = self._mark_subscribed

    async def receive(self) -> InboundMessage:
        await self._ensure_entry_id()
        while True:
            if self._event_iterator is None:
                events = self.client.events(list(self.settings.event_types))
                self._event_iterator = events.__aiter__()
            try:
                event = await self._event_iterator.__anext__()
            except StopAsyncIteration:
                self._event_iterator = None
                await asyncio.sleep(1)
                continue
            message = self._event_to_inbound(event)
            if message is not None:
                return message

    async def send(self, message: OutboundMessage) -> None:
        await self._ensure_entry_id()
        payload: dict[str, Any] = {
            "channel_idx": message.channel_index,
            "message": message.text,
        }
        if self._resolved_entry_id:
            payload["entry_id"] = self._resolved_entry_id
        await self.client.call_service(
            "meshcore",
            MESHCORE_SEND_CHANNEL_SERVICE,
            payload,
            return_response=False,
        )

    async def close(self) -> None:
        self._event_iterator = None

    async def _ensure_entry_id(self) -> None:
        if self._resolved_entry_id:
            return
        entries = await self.client.get_config_entries()
        meshcore_entries = [entry for entry in entries if entry.get("domain") == "meshcore"]
        if len(meshcore_entries) == 1:
            entry_id = meshcore_entries[0].get("entry_id")
            if isinstance(entry_id, str) and entry_id:
                self._resolved_entry_id = entry_id
                logger.info("MeshCore entry selected: %s", _redact_identifier(entry_id))
                return
        if len(meshcore_entries) > 1:
            logger.warning("Multiple MeshCore config entries found without explicit selection")
            raise RuntimeError("multiple MeshCore config entries found; set meshcore_entry_id")
        self._resolved_entry_id = None

    def _event_to_inbound(self, event: HomeAssistantEvent) -> InboundMessage | None:
        if event.event_type != MESHCORE_MESSAGE_EVENT:
            return None

        data = event.data
        if data.get("outgoing") is True:
            return None
        if data.get("message_type") != "channel":
            return None
        if int(data.get("channel_idx", -1)) != self.settings.channel_index:
            return None

        text = str(data.get("message", "")).strip()
        if not text:
            return None

        sender_id = self._sender_id(data)
        stable_sender = not sender_id.startswith("meshcore-ha:channel:")
        if self.settings.require_stable_sender and not stable_sender:
            logger.warning("Ignoring MeshCore HA channel message without stable sender identity")
            return None

        received_at = _parse_time(event.time_fired) or datetime.now(UTC)
        metadata = {
            "ha_event_type": event.event_type,
            "ha_context_id": event.context_id,
            "message_type": data.get("message_type"),
            "sender_name": data.get("sender_name"),
            "pubkey_prefix_available": bool(data.get("pubkey_prefix")),
            "stable_sender": stable_sender,
            "hop_count": data.get("hop_count"),
            "snr": data.get("snr"),
            "rx_log_count": len(data.get("rx_log_data", []))
            if isinstance(data.get("rx_log_data"), list)
            else 0,
        }
        return InboundMessage(
            transport=self.name,
            message_id=self._message_id(event, data),
            sender_id=sender_id,
            channel_index=self.settings.channel_index,
            text=text,
            received_at=received_at,
            metadata=metadata,
        )

    def _sender_id(self, data: dict[str, Any]) -> str:
        pubkey_prefix = data.get("pubkey_prefix")
        if isinstance(pubkey_prefix, str) and len(pubkey_prefix) >= 6:
            return f"meshcore-pubkey-prefix:{pubkey_prefix.lower()}"
        if self.settings.allow_channel_without_sender:
            sender_name = data.get("sender_name")
            label = sender_name if isinstance(sender_name, str) and sender_name else "unknown"
            return f"meshcore-ha:channel:{self.settings.channel_index}:{label}"
        return f"meshcore-ha:channel:{self.settings.channel_index}:unknown"

    def _message_id(self, event: HomeAssistantEvent, data: dict[str, Any]) -> str:
        if event.context_id:
            return f"ha:{event.context_id}"
        source = "|".join(
            [
                str(data.get("timestamp", "")),
                str(data.get("channel_idx", "")),
                str(data.get("sender_name", "")),
                str(data.get("message", "")),
            ]
        )
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        return f"ha-meshcore:{digest}"

    def _mark_subscribed(self) -> None:
        if not self.settings.healthcheck_path:
            return
        payload = {
            "status": "ok",
            "websocket": "connected",
            "subscription": "meshcore_message",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        path = Path(self.settings.healthcheck_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _redact_identifier(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
