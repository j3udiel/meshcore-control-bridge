from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
from dataclasses import dataclass

from meshcore_control.adapters.homeassistant_ws import HomeAssistantWebSocketClient


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    device: str
    description: str
    hwid: str


def discover_python_support() -> dict[str, bool]:
    return {
        "pyserial": importlib.util.find_spec("serial") is not None,
        "bleak": importlib.util.find_spec("bleak") is not None,
        "meshcore": importlib.util.find_spec("meshcore") is not None,
        "serial_asyncio": importlib.util.find_spec("serial_asyncio") is not None,
    }


def list_serial_ports() -> list[SerialPortInfo]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [
        SerialPortInfo(device=port.device, description=port.description, hwid=port.hwid)
        for port in list_ports.comports()
    ]


async def read_serial_probe(port: str, baudrate: int, seconds: float) -> bytes:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("pyserial is required for serial probing") from exc

    def _read() -> bytes:
        with serial.Serial(port=port, baudrate=baudrate, timeout=seconds) as handle:
            return bytes(handle.read(512))

    return await asyncio.to_thread(_read)


async def ha_inspect(
    ha_url: str,
    ha_token: str,
    verify_tls: bool,
    entry_id: str | None,
) -> int:
    client = HomeAssistantWebSocketClient(
        base_url=ha_url,
        token=ha_token,
        verify_tls=verify_tls,
    )
    services = await client.get_services()
    meshcore_services = {}
    if isinstance(services, dict):
        meshcore_services = services.get("meshcore", {})
    print("Home Assistant MeshCore integration:")
    if isinstance(meshcore_services, dict) and meshcore_services:
        print("Services:")
        for service_name in sorted(meshcore_services):
            print(f"- meshcore.{service_name}")
    else:
        print("Services: none found")

    try:
        entries = await client.get_config_entries()
    except Exception as exc:
        entries = []
        print(f"Config entries: ERROR {exc.__class__.__name__}")
    meshcore_entries = [entry for entry in entries if entry.get("domain") == "meshcore"]
    print("Config entries:")
    if not meshcore_entries:
        print("- none found through WebSocket config_entries/get")
    for entry in meshcore_entries:
        configured_entry_id = str(entry.get("entry_id", ""))
        if entry_id and configured_entry_id != entry_id:
            continue
        title = str(entry.get("title", "meshcore"))
        state = str(entry.get("state", "unknown"))
        print(
            f"- entry_id={_redact_id(configured_entry_id)} "
            f"title={_redact_optional(title)} state={state}"
        )
    print("Events to listen:")
    print("- meshcore_message")
    print("- meshcore_delivery_update")
    print("- meshcore_raw_event")
    return 0


async def ha_listen(
    ha_url: str,
    ha_token: str,
    verify_tls: bool,
    channel_index: int,
    seconds: float,
    show_message_content: bool,
) -> int:
    client = HomeAssistantWebSocketClient(
        base_url=ha_url,
        token=ha_token,
        verify_tls=verify_tls,
    )
    deadline = asyncio.get_running_loop().time() + seconds
    print(f"Listening for meshcore_message on channel {channel_index} for {seconds:g}s")
    async for event in client.events(["meshcore_message"]):
        if asyncio.get_running_loop().time() >= deadline:
            return 0
        data = event.data
        if data.get("message_type") != "channel":
            continue
        if int(data.get("channel_idx", -1)) != channel_index:
            continue
        text = str(data.get("message", ""))
        print(
            "- "
            f"type={data.get('message_type')} "
            f"channel={data.get('channel_idx')} "
            f"sender={_redact_optional(str(data.get('sender_name', '')))} "
            f"pubkey_prefix={_redact_optional(str(data.get('pubkey_prefix', '')))} "
            f"hop_count={data.get('hop_count')} "
            f"snr={data.get('snr')} "
            f"text={text if show_message_content else '<redacted>'}"
        )
    return 0


async def _legacy_serial_probe(args: argparse.Namespace) -> int:
    print("MeshCore Companion diagnostic")
    print(f"Configured admin channel index: {args.channel_index}")
    print("Python support:")
    for name, available in discover_python_support().items():
        print(f"- {name}: {'yes' if available else 'no'}")

    ports = list_serial_ports()
    print("Serial ports:")
    if not ports:
        print("- none detected")
    for port in ports:
        print(f"- {port.device}: {port.description} ({port.hwid})")

    print("Companion protocol:")
    print("- channel enumeration: not available until MeshCore protocol/library is confirmed")
    print("- message receive/send: not available until MeshCore protocol/library is confirmed")

    if args.port:
        print(f"Listening for raw serial bytes on {args.port} for {args.listen_seconds:g}s")
        data = await read_serial_probe(args.port, args.baudrate, args.listen_seconds)
        if data:
            print(f"Read {len(data)} raw bytes. Hex preview: {data[:64].hex()}")
        else:
            print("No raw bytes received.")
    return 0


def _env_token_or_arg(value: str | None) -> str:
    token = value or os.getenv("HA_TOKEN", "")
    if not token:
        raise RuntimeError("HA token is required. Pass --ha-token or set HA_TOKEN.")
    return token


def _verify_tls(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _redact_id(value: str) -> str:
    if len(value) <= 12:
        return value
    return f"{value[:8]}...{value[-4:]}"


def _redact_optional(value: str | None) -> str:
    return "<redacted>" if value else "N/D"


async def amain() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect possible MeshCore Companion connectivity without exposing secrets."
    )
    parser.add_argument("--port", help="Serial device to probe, e.g. /dev/ttyACM0")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--listen-seconds", type=float, default=5.0)
    parser.add_argument("--channel-index", type=int, default=1)
    subparsers = parser.add_subparsers(dest="command")

    ha_inspect_parser = subparsers.add_parser(
        "ha-inspect",
        help="Inspect MeshCore integration through Home Assistant WebSocket",
    )
    ha_inspect_parser.add_argument("--ha-url", default=os.getenv("HA_BASE_URL", ""))
    ha_inspect_parser.add_argument("--ha-token")
    ha_inspect_parser.add_argument("--ha-verify-tls", default=os.getenv("HA_VERIFY_TLS", "true"))
    ha_inspect_parser.add_argument("--entry-id")

    ha_listen_parser = subparsers.add_parser(
        "ha-listen",
        help="Listen for MeshCore events through Home Assistant WebSocket",
    )
    ha_listen_parser.add_argument("--ha-url", default=os.getenv("HA_BASE_URL", ""))
    ha_listen_parser.add_argument("--ha-token")
    ha_listen_parser.add_argument("--ha-verify-tls", default=os.getenv("HA_VERIFY_TLS", "true"))
    ha_listen_parser.add_argument("--channel-index", type=int, default=1)
    ha_listen_parser.add_argument("--seconds", type=float, default=60.0)
    ha_listen_parser.add_argument("--show-message-content", action="store_true")

    args = parser.parse_args()
    if args.command == "ha-inspect":
        if not args.ha_url:
            raise RuntimeError("Home Assistant URL is required. Pass --ha-url or set HA_BASE_URL.")
        return await ha_inspect(
            args.ha_url,
            _env_token_or_arg(args.ha_token),
            _verify_tls(args.ha_verify_tls),
            args.entry_id,
        )
    if args.command == "ha-listen":
        if not args.ha_url:
            raise RuntimeError("Home Assistant URL is required. Pass --ha-url or set HA_BASE_URL.")
        return await ha_listen(
            args.ha_url,
            _env_token_or_arg(args.ha_token),
            _verify_tls(args.ha_verify_tls),
            args.channel_index,
            args.seconds,
            args.show_message_content,
        )
    return await _legacy_serial_probe(args)


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
