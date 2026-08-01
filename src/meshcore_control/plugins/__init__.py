from __future__ import annotations

from meshcore_control.commands.registry import CommandRegistry
from meshcore_control.plugins import system


def build_registry() -> CommandRegistry:
    registry = CommandRegistry()
    system.register(registry)
    return registry
