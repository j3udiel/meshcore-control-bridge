from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol

from meshcore_control.bridge_health import BridgeHealthState

HEALTH_EVENT_TYPE = "meshcore_control_bridge_health"

logger = logging.getLogger(__name__)


class HomeAssistantEventClient(Protocol):
    async def fire_event(self, event_type: str, event_data: dict[str, object]) -> None: ...


@dataclass(slots=True)
class HomeAssistantHealthEventPublisher:
    health: BridgeHealthState
    client: HomeAssistantEventClient
    channel_index: int
    heartbeat_seconds: int = 60
    enabled: bool = True
    coalesce_seconds: float = 0.5
    _changed: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _critical: bool = field(default=False, init=False)
    _stop: bool = field(default=False, init=False)
    _published_fingerprint: str | None = field(default=None, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _publishing_degraded: bool = field(default=False, init=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)

    def start(self) -> None:
        if not self.enabled:
            return
        if self._task is not None and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
        self.health.set_change_callback(self.notify)
        self._task = asyncio.create_task(self.run(), name="homeassistant-health-events")
        self.notify(critical=True)

    def notify(self, critical: bool = False) -> None:
        if not self.enabled or self._stop:
            return
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._mark_changed, critical)

    def _mark_changed(self, critical: bool) -> None:
        if self._stop:
            return
        if critical:
            self._critical = True
        self._changed.set()

    async def stop(self) -> None:
        self._stop = True
        self.health.set_change_callback(None)
        self._changed.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
            self._loop = None

    async def run(self) -> None:
        next_heartbeat = asyncio.get_running_loop().time()
        try:
            while not self._stop:
                timeout = max(0.0, next_heartbeat - asyncio.get_running_loop().time())
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=timeout)
                    self._changed.clear()
                    critical = self._critical
                    self._critical = False
                    if not critical and self.coalesce_seconds > 0:
                        await asyncio.sleep(self.coalesce_seconds)
                        self._changed.clear()
                except TimeoutError:
                    critical = True
                await self.publish_if_needed(force=critical)
                next_heartbeat = asyncio.get_running_loop().time() + self.heartbeat_seconds
        except asyncio.CancelledError:
            raise

    async def publish_if_needed(self, *, force: bool = False) -> bool:
        snapshot = self.health.snapshot()
        fingerprint = snapshot.event_fingerprint(channel_index=self.channel_index)
        if not force and fingerprint == self._published_fingerprint:
            return False
        payload = snapshot.event_payload(channel_index=self.channel_index)
        try:
            await self.client.fire_event(HEALTH_EVENT_TYPE, payload)
        except Exception:
            logger.warning("Home Assistant health event publish failed reason=transport_error")
            if not self._publishing_degraded:
                self._publishing_degraded = True
                try:
                    self.health.record_failure("transport_error")
                finally:
                    self._publishing_degraded = False
            return False
        self._published_fingerprint = fingerprint
        return True
