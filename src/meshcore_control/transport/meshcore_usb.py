from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol, cast

from meshcore_control.models import InboundMessage, OutboundMessage
from meshcore_control.protocol.constants import (
    DEFAULT_MAX_FRAME_PAYLOAD,
    PACKET_CHANNEL_MSG_RECV,
    PACKET_CHANNEL_MSG_RECV_V3,
    PACKET_MESSAGES_WAITING,
    PACKET_MSG_SENT,
    PACKET_NO_MORE_MSGS,
)
from meshcore_control.protocol.decoder import decode_channel_text_message, packet_type
from meshcore_control.protocol.encoder import (
    encode_app_start,
    encode_device_query,
    encode_get_channel,
    encode_send_channel_message,
    encode_sync_next_message,
)
from meshcore_control.protocol.framing import UsbFrameParser, encode_usb_frame

logger = logging.getLogger(__name__)


class SerialHandle(Protocol):
    def read(self, size: int) -> bytes:
        raise NotImplementedError

    def write(self, data: bytes) -> int:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class SerialFactory(Protocol):
    def __call__(self, *, port: str, baudrate: int, timeout: float) -> SerialHandle:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class MeshCoreUsbSettings:
    port: str
    baudrate: int = 115200
    channel_index: int = 1
    read_timeout_seconds: float = 0.25
    command_timeout_seconds: float = 5.0
    max_frame_payload: int = DEFAULT_MAX_FRAME_PAYLOAD
    app_name: str = "meshcore-control-bridge"


class MeshCoreUsbSession:
    def __init__(
        self,
        settings: MeshCoreUsbSettings,
        *,
        serial_factory: SerialFactory | None = None,
    ) -> None:
        self.settings = settings
        self._serial_factory = serial_factory or _default_serial_factory
        self._serial: SerialHandle | None = None
        self._parser = UsbFrameParser(max_payload_size=settings.max_frame_payload)
        self._write_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._serial is not None

    async def connect(self) -> None:
        if self._serial is not None:
            return

        def _open() -> SerialHandle:
            return self._serial_factory(
                port=self.settings.port,
                baudrate=self.settings.baudrate,
                timeout=self.settings.read_timeout_seconds,
            )

        self._serial = await asyncio.to_thread(_open)
        await self.command(encode_app_start(self.settings.app_name), expected={0x05})
        await self.command(encode_device_query(), expected={0x0D})
        await self.command(encode_get_channel(self.settings.channel_index), expected={0x12})

    async def command(self, payload: bytes, *, expected: set[int]) -> bytes:
        async with self._write_lock:
            await self._write_frame(payload)
            deadline = asyncio.get_running_loop().time() + self.settings.command_timeout_seconds
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for MeshCore Companion response")
                frame = await asyncio.wait_for(self.read_frame(), timeout=remaining)
                kind = packet_type(frame)
                if kind in expected:
                    return frame
                if kind == PACKET_MESSAGES_WAITING:
                    continue
                logger.debug("Ignoring unsolicited MeshCore packet type %s during command", kind)

    async def read_frame(self) -> bytes:
        if self._serial is None:
            raise RuntimeError("MeshCore USB session is not connected")
        while True:
            chunk = await asyncio.to_thread(self._serial.read, 256)
            if not chunk:
                await asyncio.sleep(0)
                continue
            frames = self._parser.feed(chunk)
            if frames:
                return frames[0]

    async def poll_channel_message(self) -> InboundMessage | None:
        frame = await self.command(
            encode_sync_next_message(),
            expected={
                PACKET_CHANNEL_MSG_RECV,
                PACKET_CHANNEL_MSG_RECV_V3,
                PACKET_NO_MORE_MSGS,
            },
        )
        if packet_type(frame) == PACKET_NO_MORE_MSGS:
            return None

        channel_message = decode_channel_text_message(frame)
        metadata = {
            "timestamp": channel_message.timestamp,
            "path_len": channel_message.path_len,
            "text_type": channel_message.text_type,
            "snr": channel_message.snr,
            "sender_id_available": False,
            "sender_id_note": "MeshCore channel text frames do not expose a stable sender id",
        }
        return InboundMessage(
            transport="meshcore-usb",
            message_id=channel_message.synthetic_message_id,
            sender_id=channel_message.synthetic_sender_id,
            channel_index=channel_message.channel_index,
            text=channel_message.text,
            metadata=metadata,
        )

    async def send_channel_message(self, message: OutboundMessage) -> None:
        payload = encode_send_channel_message(
            channel_index=message.channel_index,
            text=message.text,
        )
        await self.command(payload, expected={PACKET_MSG_SENT})

    async def _write_frame(self, payload: bytes) -> None:
        if self._serial is None:
            raise RuntimeError("MeshCore USB session is not connected")
        frame = encode_usb_frame(payload)
        serial_handle = self._serial

        def _write() -> None:
            written = serial_handle.write(frame)
            if written != len(frame):
                raise OSError("short serial write to MeshCore Companion")

        await asyncio.to_thread(_write)

    async def close(self) -> None:
        serial = self._serial
        self._serial = None
        if serial is not None:
            await asyncio.to_thread(serial.close)


class MeshCoreUSBTransport:
    name = "meshcore-usb"

    def __init__(
        self,
        *,
        settings: MeshCoreUsbSettings,
        reconnect_initial_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        serial_factory: SerialFactory | None = None,
    ) -> None:
        self.settings = settings
        self.reconnect_initial_seconds = reconnect_initial_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self.session = MeshCoreUsbSession(settings, serial_factory=serial_factory)

    async def receive(self) -> InboundMessage:
        delay = self.reconnect_initial_seconds
        while True:
            try:
                if not self.session.connected:
                    await self.session.connect()
                    delay = self.reconnect_initial_seconds
                frame = await self.session.read_frame()
                kind = packet_type(frame)
                if kind == PACKET_MESSAGES_WAITING:
                    message = await self.session.poll_channel_message()
                    if message is not None:
                        return message
                if kind in {PACKET_CHANNEL_MSG_RECV, PACKET_CHANNEL_MSG_RECV_V3}:
                    channel_message = decode_channel_text_message(frame)
                    return InboundMessage(
                        transport=self.name,
                        message_id=channel_message.synthetic_message_id,
                        sender_id=channel_message.synthetic_sender_id,
                        channel_index=channel_message.channel_index,
                        text=channel_message.text,
                        metadata={
                            "timestamp": channel_message.timestamp,
                            "path_len": channel_message.path_len,
                            "text_type": channel_message.text_type,
                            "snr": channel_message.snr,
                            "sender_id_available": False,
                        },
                    )
            except Exception as exc:
                logger.warning("MeshCore USB receive failed: %s", exc.__class__.__name__)
                await self.session.close()
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.reconnect_max_seconds)

    async def send(self, message: OutboundMessage) -> None:
        if not self.session.connected:
            await self.session.connect()
        await self.session.send_channel_message(message)

    async def close(self) -> None:
        await self.session.close()


def _default_serial_factory(*, port: str, baudrate: int, timeout: float) -> SerialHandle:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("pyserial is required for MeshCore USB transport") from exc
    return cast(SerialHandle, serial.Serial(port=port, baudrate=baudrate, timeout=timeout))
