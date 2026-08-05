from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from meshcore_control import __version__

logger = logging.getLogger(__name__)

_SAFE_FAILURE_REASONS = {
    "none",
    "database_locked",
    "storage_error",
    "transport_error",
    "rate_limited",
    "consumer_conflict",
    "websocket_disconnected",
    "telegram_disabled",
    "telegram_not_configured",
    "shutdown",
}


@dataclass(frozen=True, slots=True)
class BridgeHealthSnapshot:
    version: str
    started_at: datetime
    uptime_seconds: int
    ha_websocket_state: str
    meshcore_transport_state: str
    telegram_polling_state: str
    telegram_enabled: bool
    forward_telegram_to_meshcore: bool
    forward_meshcore_to_telegram: bool
    forward_confirmation_enabled: bool
    last_tg_to_mc: datetime | None
    last_mc_to_tg: datetime | None
    last_failure: datetime | None
    last_failure_reason: str
    tg_to_mc_success: int
    tg_to_mc_failed: int
    mc_to_tg_success: int
    mc_to_tg_failed: int
    commands_processed: int
    audit_db_health: str
    telegram_db_health: str

    @property
    def status(self) -> str:
        degraded = (
            self.meshcore_transport_state == "disconnected"
            or self.telegram_polling_state == "degraded"
            or self.audit_db_health == "degraded"
            or self.telegram_db_health == "degraded"
        )
        return "degraded" if degraded else "ok"

    def event_payload(self, *, channel_index: int) -> dict[str, Any]:
        return {
            "status": self.status,
            "version": self.version,
            "uptime_seconds": self.uptime_seconds,
            "meshcore": self.meshcore_transport_state,
            "telegram": _telegram_state(self),
            "channel": channel_index,
            "forwarding": {
                "telegram_to_meshcore": self.forward_telegram_to_meshcore,
                "meshcore_to_telegram": self.forward_meshcore_to_telegram,
                "confirmation": self.forward_confirmation_enabled,
            },
            "database": {
                "audit": self.audit_db_health,
                "telegram": self.telegram_db_health,
            },
            "counters": {
                "tg_to_mc_success": self.tg_to_mc_success,
                "tg_to_mc_failed": self.tg_to_mc_failed,
                "mc_to_tg_success": self.mc_to_tg_success,
                "mc_to_tg_failed": self.mc_to_tg_failed,
                "commands_processed": self.commands_processed,
            },
            "last_activity": {
                "telegram_to_meshcore": _optional_rfc3339(self.last_tg_to_mc),
                "meshcore_to_telegram": _optional_rfc3339(self.last_mc_to_tg),
            },
            "last_error": {
                "timestamp": _optional_rfc3339(self.last_failure),
                "reason": self.last_failure_reason,
            },
        }

    def event_fingerprint(self, *, channel_index: int) -> str:
        payload = self.event_payload(channel_index=channel_index)
        payload.pop("uptime_seconds", None)
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    def critical_transition_from(self, previous: BridgeHealthSnapshot | None) -> bool:
        if previous is None:
            return True
        if self.status != previous.status:
            return True
        if (
            self.meshcore_transport_state == "disconnected"
            and previous.meshcore_transport_state != "disconnected"
        ):
            return True
        if self.telegram_polling_state in {"degraded", "disconnected"} and (
            self.telegram_polling_state != previous.telegram_polling_state
        ):
            return True
        return self.last_failure != previous.last_failure


@dataclass(slots=True)
class BridgeHealthState:
    version: str = __version__
    healthcheck_path: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _ha_websocket_state: str = "disconnected"
    _meshcore_transport_state: str = "disconnected"
    _telegram_polling_state: str = "disabled"
    _telegram_enabled: bool = False
    _forward_telegram_to_meshcore: bool = False
    _forward_meshcore_to_telegram: bool = False
    _forward_confirmation_enabled: bool = False
    _last_tg_to_mc: datetime | None = None
    _last_mc_to_tg: datetime | None = None
    _last_failure: datetime | None = None
    _last_failure_reason: str = "none"
    _tg_to_mc_success: int = 0
    _tg_to_mc_failed: int = 0
    _mc_to_tg_success: int = 0
    _mc_to_tg_failed: int = 0
    _commands_processed: int = 0
    _audit_db_health: str = "ok"
    _telegram_db_health: str = "ok"
    _change_callback: Callable[[bool], None] | None = field(default=None, init=False, repr=False)

    def set_change_callback(self, callback: Callable[[bool], None] | None) -> None:
        with self._lock:
            self._change_callback = callback

    def configure(
        self,
        *,
        telegram_enabled: bool,
        forward_telegram_to_meshcore: bool,
        forward_meshcore_to_telegram: bool,
        forward_confirmation_enabled: bool,
    ) -> None:
        previous = self.snapshot()
        with self._lock:
            self._telegram_enabled = telegram_enabled
            self._telegram_polling_state = "disconnected" if telegram_enabled else "disabled"
            self._forward_telegram_to_meshcore = forward_telegram_to_meshcore
            self._forward_meshcore_to_telegram = forward_meshcore_to_telegram
            self._forward_confirmation_enabled = forward_confirmation_enabled
        self._after_change(previous)

    def set_meshcore_connected(self, connected: bool) -> None:
        previous = self.snapshot()
        with self._lock:
            self._meshcore_transport_state = "connected" if connected else "disconnected"
            self._ha_websocket_state = "connected" if connected else "disconnected"
        self._after_change(previous)

    def set_telegram_polling(self, state: str) -> None:
        if state not in {"disabled", "connected", "degraded", "disconnected"}:
            state = "degraded"
        previous = self.snapshot()
        with self._lock:
            self._telegram_polling_state = state
        self._after_change(previous)

    def record_command_processed(self) -> None:
        previous = self.snapshot()
        with self._lock:
            self._commands_processed += 1
        self._after_change(previous)

    def record_tg_to_mc(self, *, success: bool, reason: str = "none") -> None:
        previous = self.snapshot()
        with self._lock:
            if success:
                self._tg_to_mc_success += 1
                self._last_tg_to_mc = datetime.now(UTC)
            else:
                self._tg_to_mc_failed += 1
                self._record_failure_locked(reason)
        self._after_change(previous)

    def record_mc_to_tg(self, *, success: bool, reason: str = "none") -> None:
        previous = self.snapshot()
        with self._lock:
            if success:
                self._mc_to_tg_success += 1
                self._last_mc_to_tg = datetime.now(UTC)
            else:
                self._mc_to_tg_failed += 1
                self._record_failure_locked(reason)
        self._after_change(previous)

    def set_audit_db_health(self, state: str, *, reason: str | None = None) -> None:
        self._set_db_health("audit", state, reason=reason)

    def set_telegram_db_health(self, state: str, *, reason: str | None = None) -> None:
        self._set_db_health("telegram", state, reason=reason)

    def record_failure(self, reason: str) -> None:
        previous = self.snapshot()
        with self._lock:
            self._record_failure_locked(reason)
        self._after_change(previous)

    def snapshot(self) -> BridgeHealthSnapshot:
        with self._lock:
            now = datetime.now(UTC)
            return BridgeHealthSnapshot(
                version=self.version,
                started_at=self.started_at,
                uptime_seconds=max(0, int((now - self.started_at).total_seconds())),
                ha_websocket_state=self._ha_websocket_state,
                meshcore_transport_state=self._meshcore_transport_state,
                telegram_polling_state=self._telegram_polling_state,
                telegram_enabled=self._telegram_enabled,
                forward_telegram_to_meshcore=self._forward_telegram_to_meshcore,
                forward_meshcore_to_telegram=self._forward_meshcore_to_telegram,
                forward_confirmation_enabled=self._forward_confirmation_enabled,
                last_tg_to_mc=self._last_tg_to_mc,
                last_mc_to_tg=self._last_mc_to_tg,
                last_failure=self._last_failure,
                last_failure_reason=self._last_failure_reason,
                tg_to_mc_success=self._tg_to_mc_success,
                tg_to_mc_failed=self._tg_to_mc_failed,
                mc_to_tg_success=self._mc_to_tg_success,
                mc_to_tg_failed=self._mc_to_tg_failed,
                commands_processed=self._commands_processed,
                audit_db_health=self._audit_db_health,
                telegram_db_health=self._telegram_db_health,
            )

    def health_payload(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            "status": snapshot.status,
            "version": snapshot.version,
            "updated_at": _rfc3339(datetime.now(UTC)),
            "started_at": _rfc3339(snapshot.started_at),
            "uptime_seconds": snapshot.uptime_seconds,
            "homeassistant_websocket": snapshot.ha_websocket_state,
            "meshcore": snapshot.meshcore_transport_state,
            "telegram": _telegram_state(snapshot),
            "forwarding": {
                "telegram_to_meshcore": snapshot.forward_telegram_to_meshcore,
                "meshcore_to_telegram": snapshot.forward_meshcore_to_telegram,
                "telegram_confirmation": snapshot.forward_confirmation_enabled,
            },
            "last_tg_to_mc": _optional_rfc3339(snapshot.last_tg_to_mc),
            "last_mc_to_tg": _optional_rfc3339(snapshot.last_mc_to_tg),
            "last_error": snapshot.last_failure_reason,
            "counters": {
                "tg_to_mc_success": snapshot.tg_to_mc_success,
                "tg_to_mc_failed": snapshot.tg_to_mc_failed,
                "mc_to_tg_success": snapshot.mc_to_tg_success,
                "mc_to_tg_failed": snapshot.mc_to_tg_failed,
                "commands_processed": snapshot.commands_processed,
            },
            "databases": {
                "audit": snapshot.audit_db_health,
                "telegram": snapshot.telegram_db_health,
            },
        }

    def write_healthcheck(self) -> None:
        if not self.healthcheck_path:
            return
        path = Path(self.healthcheck_path)
        temp_name: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self.health_payload(), ensure_ascii=True, sort_keys=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=str(path.parent),
                text=True,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            temp_name = None
        except OSError:
            logger.warning("Bridge healthcheck write skipped reason=storage_error")
        finally:
            if temp_name is not None:
                try:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
                except OSError:
                    logger.warning("Bridge healthcheck temp cleanup skipped reason=storage_error")

    def _set_db_health(self, db_name: str, state: str, *, reason: str | None) -> None:
        if state not in {"ok", "degraded"}:
            state = "degraded"
        previous = self.snapshot()
        with self._lock:
            if db_name == "audit":
                self._audit_db_health = state
            else:
                self._telegram_db_health = state
            if state == "degraded":
                self._record_failure_locked(reason or "storage_error")
        self._after_change(previous)

    def _record_failure_locked(self, reason: str) -> None:
        self._last_failure = datetime.now(UTC)
        self._last_failure_reason = _safe_reason(reason)

    def _after_change(self, previous: BridgeHealthSnapshot | None) -> None:
        self.write_healthcheck()
        callback: Callable[[bool], None] | None
        snapshot = self.snapshot()
        critical = snapshot.critical_transition_from(previous)
        with self._lock:
            callback = self._change_callback
        if callback is not None:
            callback(critical)


def render_bridge_status(snapshot: BridgeHealthSnapshot, *, compact: bool = False) -> str:
    telegram = _telegram_state(snapshot)
    if compact:
        return "\n".join(
            [
                f"Bridge {snapshot.version}",
                f"MC:{_short_connected(snapshot.meshcore_transport_state)} "
                f"TG:{_short_telegram(telegram)} CH:{_channel_unknown()}",
                f"T2M:{_on_off(snapshot.forward_telegram_to_meshcore)} "
                f"M2T:{_on_off(snapshot.forward_meshcore_to_telegram)} "
                f"CF:{_on_off(snapshot.forward_confirmation_enabled)}",
                f"DB A:{snapshot.audit_db_health} T:{snapshot.telegram_db_health}",
                f"Err:{snapshot.last_failure_reason}",
            ]
        )
    return "\n".join(
        [
            f"Version: {snapshot.version}",
            f"MeshCore: {snapshot.meshcore_transport_state}",
            f"Telegram: {telegram}",
            "Channel: {channel}",
            f"TG->MC: {_on_off(snapshot.forward_telegram_to_meshcore)}",
            f"MC->TG: {_on_off(snapshot.forward_meshcore_to_telegram)}",
            f"TG confirm: {_on_off(snapshot.forward_confirmation_enabled)}",
            f"Audit DB: {snapshot.audit_db_health}",
            f"Telegram DB: {snapshot.telegram_db_health}",
            f"Last error: {snapshot.last_failure_reason}",
        ]
    )


def render_bridge_status_for_channel(
    snapshot: BridgeHealthSnapshot,
    *,
    channel_index: int,
    compact: bool = False,
    health_events_enabled: bool | None = None,
    heartbeat_seconds: int | None = None,
) -> str:
    text = render_bridge_status(snapshot, compact=compact).replace(
        "{channel}",
        str(channel_index),
    ).replace("CH:?", f"CH:{channel_index}")
    if health_events_enabled is None or heartbeat_seconds is None:
        return text
    if compact:
        return f"{text}\nHAE:{_on_off(health_events_enabled)} HB:{heartbeat_seconds}"
    return "\n".join(
        [
            text,
            f"HA events: {_on_off(health_events_enabled)}",
            f"Heartbeat: {heartbeat_seconds}s",
        ]
    )


def render_last_activity(
    snapshot: BridgeHealthSnapshot,
    *,
    now: datetime | None = None,
    compact: bool = False,
) -> str:
    reference = _utc_now(now)
    tg_to_mc = relative_time(snapshot.last_tg_to_mc, now=reference)
    mc_to_tg = relative_time(snapshot.last_mc_to_tg, now=reference)
    last_error_time = relative_time(snapshot.last_failure, now=reference)
    uptime = relative_duration(timedelta(seconds=snapshot.uptime_seconds), compact=compact)
    reason = snapshot.last_failure_reason

    if compact:
        return "\n".join(
            [
                "Last",
                f"T2M:{tg_to_mc} M2T:{mc_to_tg}",
                f"OK:{snapshot.tg_to_mc_success}/{snapshot.mc_to_tg_success} "
                f"F:{snapshot.tg_to_mc_failed}/{snapshot.mc_to_tg_failed}",
                f"Cmd:{snapshot.commands_processed} Up:{uptime}",
                f"Err:{reason}",
            ]
        )

    return "\n".join(
        [
            "Last activity",
            "",
            f"TG -> MC: {tg_to_mc}",
            f"MC -> TG: {mc_to_tg}",
            f"TG -> MC: {snapshot.tg_to_mc_success} success / {snapshot.tg_to_mc_failed} failed",
            f"MC -> TG: {snapshot.mc_to_tg_success} success / {snapshot.mc_to_tg_failed} failed",
            f"Commands: {snapshot.commands_processed}",
            f"Last error: {reason}",
            f"Last error time: {last_error_time}",
            f"Uptime: {uptime}",
        ]
    )


def relative_time(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return "never"
    reference = _utc_now(now)
    current = _utc_now(value)
    delta_seconds = int((reference - current).total_seconds())
    if delta_seconds <= 0:
        return "now"
    return f"{relative_duration(timedelta(seconds=delta_seconds), compact=True)} ago"


def relative_duration(value: timedelta, *, compact: bool = False) -> str:
    seconds = max(0, int(value.total_seconds()))
    if seconds == 0:
        return "now"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if hours < 24:
        separator = "" if compact else " "
        return f"{hours}h{separator}{remaining_minutes}m"
    days = hours // 24
    remaining_hours = hours % 24
    separator = "" if compact else " "
    return f"{days}d{separator}{remaining_hours}h"


def _telegram_state(snapshot: BridgeHealthSnapshot) -> str:
    if not snapshot.telegram_enabled:
        return "disabled"
    if snapshot.telegram_polling_state == "connected":
        return "connected"
    if snapshot.telegram_polling_state in {"disabled", "disconnected"}:
        return "enabled"
    return "degraded"


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def _short_connected(value: str) -> str:
    return "on" if value == "connected" else "off"


def _short_telegram(value: str) -> str:
    return {"disabled": "off", "connected": "on"}.get(value, "deg")


def _channel_unknown() -> str:
    return "?"


def _safe_reason(reason: str) -> str:
    normalized = reason.strip().lower().replace(" ", "_")
    return normalized if normalized in _SAFE_FAILURE_REASONS else "storage_error"


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return current.astimezone(UTC)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _optional_rfc3339(value: datetime | None) -> str | None:
    return _rfc3339(value) if value is not None else None
