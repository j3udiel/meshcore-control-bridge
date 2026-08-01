from __future__ import annotations

from collections.abc import Iterable

from meshcore_control.protocol.constants import (
    DEFAULT_MAX_FRAME_PAYLOAD,
    USB_APP_TO_RADIO_PREFIX,
    USB_RADIO_TO_APP_PREFIX,
)


class FrameLengthError(ValueError):
    """Raised when a USB frame declares an unsafe length."""


def encode_usb_frame(payload: bytes, *, radio_to_app: bool = False) -> bytes:
    prefix = USB_RADIO_TO_APP_PREFIX if radio_to_app else USB_APP_TO_RADIO_PREFIX
    if len(payload) > 0xFFFF:
        raise FrameLengthError("USB frame payload exceeds uint16 length")
    return bytes([prefix]) + len(payload).to_bytes(2, "little") + payload


class UsbFrameParser:
    def __init__(
        self,
        *,
        expected_prefix: int = USB_RADIO_TO_APP_PREFIX,
        max_payload_size: int = DEFAULT_MAX_FRAME_PAYLOAD,
    ) -> None:
        self.expected_prefix = expected_prefix
        self.max_payload_size = max_payload_size
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        frames: list[bytes] = []

        while True:
            prefix_index = self._find_prefix()
            if prefix_index is None:
                self._buffer.clear()
                return frames
            if prefix_index > 0:
                del self._buffer[:prefix_index]
            if len(self._buffer) < 3:
                return frames

            length = int.from_bytes(self._buffer[1:3], "little")
            if length > self.max_payload_size:
                del self._buffer[0]
                raise FrameLengthError(
                    f"USB frame payload length {length} exceeds {self.max_payload_size}"
                )
            if len(self._buffer) < 3 + length:
                return frames

            start = 3
            end = start + length
            frames.append(bytes(self._buffer[start:end]))
            del self._buffer[:end]

    def _find_prefix(self) -> int | None:
        try:
            return self._buffer.index(self.expected_prefix)
        except ValueError:
            return None


def parse_stream_chunks(chunks: Iterable[bytes], *, max_payload_size: int) -> list[bytes]:
    parser = UsbFrameParser(max_payload_size=max_payload_size)
    frames: list[bytes] = []
    for chunk in chunks:
        frames.extend(parser.feed(chunk))
    return frames

