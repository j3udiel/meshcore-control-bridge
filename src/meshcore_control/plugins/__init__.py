from __future__ import annotations

from meshcore_control.commands.registry import CommandRegistry
from meshcore_control.plugins import house, network, servers, system


def build_registry() -> CommandRegistry:
    registry = CommandRegistry()
    system.register(registry)
    house.register(registry)
    servers.register(registry)
    network.register(registry)
    return registry
