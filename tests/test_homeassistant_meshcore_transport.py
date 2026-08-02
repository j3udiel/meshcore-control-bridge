from __future__ import annotations

import asyncio

from meshcore_control.adapters.homeassistant_ws import HomeAssistantEvent
from meshcore_control.models import OutboundMessage
from meshcore_control.transport.homeassistant_meshcore import (
    HomeAssistantMeshCoreSettings,
    HomeAssistantMeshCoreTransport,
)


class FakeHaWsClient:
    def __init__(self, events: list[HomeAssistantEvent] | None = None) -> None:
        self._events = events or []
        self.service_calls: list[tuple[str, str, dict[str, object], bool]] = []
        self.config_entries: list[dict[str, object]] = []

    async def events(self, event_types: list[str]):
        for event in self._events:
            yield event

    async def get_config_entries(self) -> list[dict[str, object]]:
        return self.config_entries

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, object],
        *,
        return_response: bool = False,
    ) -> None:
        self.service_calls.append((domain, service, service_data, return_response))


def test_ha_meshcore_event_with_pubkey_prefix_becomes_inbound() -> None:
    client = FakeHaWsClient(
        [
            HomeAssistantEvent(
                event_type="meshcore_message",
                data={
                    "message_type": "channel",
                    "channel_idx": 1,
                    "message": "!ping",
                    "sender_name": "admin-device",
                    "pubkey_prefix": "ABCDEF123456",
                    "hop_count": 1,
                    "snr": 8,
                },
                time_fired="2026-08-02T10:00:00+00:00",
                context_id="ctx-1",
            )
        ]
    )
    transport = HomeAssistantMeshCoreTransport(
        settings=HomeAssistantMeshCoreSettings(
            channel_index=1,
            ha_base_url="http://homeassistant.local:8123",
            ha_token="test-token-not-real",
        ),
        websocket_client=client,  # type: ignore[arg-type]
    )

    inbound = asyncio.run(transport.receive())

    assert inbound.text == "!ping"
    assert inbound.message_id == "ha:ctx-1"
    assert inbound.sender_id == "meshcore-pubkey-prefix:abcdef123456"
    assert inbound.channel_index == 1
    assert inbound.metadata["stable_sender"] is True
    assert inbound.source_room is not None
    assert inbound.source_room.transport == "homeassistant-meshcore"
    assert inbound.source_room.room_id == "homeassistant-meshcore:channel:1"
    assert inbound.reply_target == inbound.source_room
    assert inbound.sender is not None
    assert inbound.sender.identity_kind == "meshcore_pubkey_prefix"
    assert inbound.sender.transport_scope == "homeassistant-meshcore"
    assert inbound.message is not None
    assert inbound.message.id_kind == "platform"
    assert inbound.message.origin.transport == "homeassistant-meshcore"


def test_ha_meshcore_ignores_other_channel() -> None:
    client = FakeHaWsClient(
        [
            HomeAssistantEvent(
                event_type="meshcore_message",
                data={
                    "message_type": "channel",
                    "channel_idx": 2,
                    "message": "!ping",
                    "pubkey_prefix": "abcdef123456",
                },
            )
        ]
    )
    transport = HomeAssistantMeshCoreTransport(
        settings=HomeAssistantMeshCoreSettings(
            channel_index=1,
            ha_base_url="http://homeassistant.local:8123",
            ha_token="test-token-not-real",
        ),
        websocket_client=client,  # type: ignore[arg-type]
    )

    async def receive_with_timeout() -> object:
        return await asyncio.wait_for(transport.receive(), timeout=0.05)

    try:
        asyncio.run(receive_with_timeout())
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected receive timeout")


def test_ha_meshcore_ignores_outgoing_event() -> None:
    transport = HomeAssistantMeshCoreTransport(
        settings=HomeAssistantMeshCoreSettings(
            channel_index=1,
            ha_base_url="http://homeassistant.local:8123",
            ha_token="test-token-not-real",
        )
    )

    inbound = transport._event_to_inbound(
        HomeAssistantEvent(
            event_type="meshcore_message",
            data={
                "message_type": "channel",
                "channel_idx": 1,
                "message": "pong",
                "outgoing": True,
                "pubkey_prefix": "abcdef123456",
            },
        )
    )

    assert inbound is None


def test_ha_meshcore_requires_stable_sender_by_default() -> None:
    transport = HomeAssistantMeshCoreTransport(
        settings=HomeAssistantMeshCoreSettings(
            channel_index=1,
            ha_base_url="http://homeassistant.local:8123",
            ha_token="test-token-not-real",
        )
    )

    inbound = transport._event_to_inbound(
        HomeAssistantEvent(
            event_type="meshcore_message",
            data={"message_type": "channel", "channel_idx": 1, "message": "!ping"},
        )
    )

    assert inbound is None


def test_ha_meshcore_can_allow_channel_without_sender_for_testing() -> None:
    transport = HomeAssistantMeshCoreTransport(
        settings=HomeAssistantMeshCoreSettings(
            channel_index=1,
            ha_base_url="http://homeassistant.local:8123",
            ha_token="test-token-not-real",
            require_stable_sender=False,
            allow_channel_without_sender=True,
        )
    )

    inbound = transport._event_to_inbound(
        HomeAssistantEvent(
            event_type="meshcore_message",
            data={
                "message_type": "channel",
                "channel_idx": 1,
                "message": "!ping",
                "sender_name": "admin-device",
            },
        )
    )

    assert inbound is not None
    assert inbound.sender_id == "test:unidentified:channel:1"
    assert inbound.metadata["stable_sender"] is False


def test_ha_meshcore_send_uses_official_channel_service() -> None:
    client = FakeHaWsClient()
    transport = HomeAssistantMeshCoreTransport(
        settings=HomeAssistantMeshCoreSettings(
            channel_index=1,
            ha_base_url="http://homeassistant.local:8123",
            ha_token="test-token-not-real",
            ha_entry_id="entry-id",
        ),
        websocket_client=client,  # type: ignore[arg-type]
    )

    asyncio.run(transport.send(OutboundMessage(destination="sender", channel_index=1, text="pong")))

    assert client.service_calls == [
        (
            "meshcore",
            "send_channel_message",
            {"channel_idx": 1, "message": "pong", "entry_id": "entry-id"},
            False,
        )
    ]


def test_ha_meshcore_rejects_multiple_entries_without_selection() -> None:
    client = FakeHaWsClient()
    client.config_entries = [
        {"domain": "meshcore", "entry_id": "one"},
        {"domain": "meshcore", "entry_id": "two"},
    ]
    transport = HomeAssistantMeshCoreTransport(
        settings=HomeAssistantMeshCoreSettings(
            channel_index=1,
            ha_base_url="http://homeassistant.local:8123",
            ha_token="test-token-not-real",
        ),
        websocket_client=client,  # type: ignore[arg-type]
    )

    async def send() -> None:
        await transport.send(OutboundMessage(destination="sender", channel_index=1, text="pong"))

    try:
        asyncio.run(send())
    except RuntimeError as exc:
        assert "multiple MeshCore" in str(exc)
    else:
        raise AssertionError("expected multiple entry failure")
