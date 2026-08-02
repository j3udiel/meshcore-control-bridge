from __future__ import annotations

from meshcore_control.adapters.servers.base import ServerProvider


class ProxmoxProvider(ServerProvider):
    """Future Proxmox API provider. No shell access is implemented."""
