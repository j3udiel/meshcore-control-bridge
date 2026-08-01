from __future__ import annotations

import argparse
import asyncio
import importlib.util
from dataclasses import dataclass


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


async def amain() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect possible MeshCore Companion connectivity without exposing secrets."
    )
    parser.add_argument("--port", help="Serial device to probe, e.g. /dev/ttyACM0")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--listen-seconds", type=float, default=5.0)
    parser.add_argument("--channel-index", type=int, default=1)
    args = parser.parse_args()

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


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
