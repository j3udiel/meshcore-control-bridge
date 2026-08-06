from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import stat
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any

from meshcore_control.models import InboundMessage, RoomRef
from meshcore_control.storage.database import write_transaction

logger = logging.getLogger(__name__)

NORMALIZED_AUDIT_SCHEMA_VERSION = 1
AUDIT_KEY_MIN_BYTES = 32
AUDIT_METADATA_MAX_BYTES = 4096
AUDIT_METADATA_MAX_DEPTH = 4
AUDIT_METADATA_MAX_STRING_LENGTH = 256
AUDIT_METADATA_MAX_ITEMS = 32
APP_AUDIT_KEY_PATH = "/data/audit.key"
AUDIT_REFERENCE_PATTERN = re.compile(r"^hmac-sha256:v1:([A-Za-z0-9_-]+):[0-9a-f]{64}$")
EVENT_ID_PATTERN = re.compile(r"^evt:[0-9a-f]{32}$")
CORRELATION_ID_PATTERN = re.compile(r"^corr:[0-9a-f]{32}$")


class NormalizedAuditDisabled(RuntimeError):
    pass


class AuditKeyError(RuntimeError):
    pass


class NormalizedAuditEventType(StrEnum):
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_IGNORED = "message.ignored"
    COMMAND_PARSED = "command.parsed"
    COMMAND_AUTHORIZATION = "command.authorization"
    COMMAND_EXECUTION = "command.execution"
    RESPONSE_SENT = "response.sent"
    RESPONSE_FAILED = "response.failed"
    BRIDGE_MESSAGE_RECEIVED = "bridge.message.received"
    BRIDGE_MESSAGE_FORWARDED = "bridge.message.forwarded"
    BRIDGE_MESSAGE_IGNORED = "bridge.message.ignored"
    BRIDGE_MESSAGE_FAILED = "bridge.message.failed"
    BRIDGE_RUNTIME_OVERRIDE_REQUESTED = "bridge.runtime_override.requested"
    BRIDGE_RUNTIME_OVERRIDE_APPLIED = "bridge.runtime_override.applied"
    BRIDGE_RUNTIME_OVERRIDE_DENIED = "bridge.runtime_override.denied"
    BRIDGE_RUNTIME_OVERRIDE_RESET = "bridge.runtime_override.reset"


METADATA_ALLOWLIST: dict[NormalizedAuditEventType, frozenset[str]] = {
    NormalizedAuditEventType.MESSAGE_RECEIVED: frozenset(
        {
            "channel_index",
            "message_id_present",
            "identity_kind",
            "identity_stable",
        }
    ),
    NormalizedAuditEventType.MESSAGE_IGNORED: frozenset(
        {
            "channel_index",
            "ignore_reason",
            "deduplication_result",
            "rate_limit_result",
        }
    ),
    NormalizedAuditEventType.COMMAND_PARSED: frozenset(
        {
            "parse_result",
            "argument_count",
        }
    ),
    NormalizedAuditEventType.COMMAND_AUTHORIZATION: frozenset(
        {
            "authorization_result",
            "authorization_reason",
        }
    ),
    NormalizedAuditEventType.COMMAND_EXECUTION: frozenset(
        {
            "command_result",
        }
    ),
    NormalizedAuditEventType.RESPONSE_SENT: frozenset(
        {
            "transport_service",
            "response_length",
        }
    ),
    NormalizedAuditEventType.RESPONSE_FAILED: frozenset(
        {
            "transport_service",
        }
    ),
    NormalizedAuditEventType.BRIDGE_MESSAGE_RECEIVED: frozenset(
        {
            "direction",
            "source_transport",
            "destination_transport",
            "size_bytes",
        }
    ),
    NormalizedAuditEventType.BRIDGE_MESSAGE_FORWARDED: frozenset(
        {
            "direction",
            "source_transport",
            "destination_transport",
            "result",
            "size_bytes",
            "truncated",
        }
    ),
    NormalizedAuditEventType.BRIDGE_MESSAGE_IGNORED: frozenset(
        {
            "direction",
            "source_transport",
            "destination_transport",
            "reason",
            "size_bytes",
            "truncated",
        }
    ),
    NormalizedAuditEventType.BRIDGE_MESSAGE_FAILED: frozenset(
        {
            "direction",
            "source_transport",
            "destination_transport",
            "result",
            "reason",
            "size_bytes",
            "truncated",
        }
    ),
    NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_REQUESTED: frozenset(
        {
            "operation",
            "target",
            "requested_value",
            "transport",
        }
    ),
    NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_APPLIED: frozenset(
        {
            "operation",
            "target",
            "previous_value",
            "new_value",
            "override_value",
            "transport",
            "result",
        }
    ),
    NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_DENIED: frozenset(
        {
            "operation",
            "target",
            "requested_value",
            "transport",
            "reason",
        }
    ),
    NormalizedAuditEventType.BRIDGE_RUNTIME_OVERRIDE_RESET: frozenset(
        {
            "operation",
            "target",
            "previous_value",
            "new_value",
            "override_value",
            "transport",
            "result",
        }
    ),
}

COMMAND_PARSE_RESULTS = frozenset({"recognized", "unknown", "malformed", "not_a_command"})


def new_event_id() -> str:
    return f"evt:{uuid.uuid4().hex}"


def new_correlation_id() -> str:
    return f"corr:{uuid.uuid4().hex}"


@dataclass(frozen=True, slots=True)
class AuditKey:
    key: bytes
    key_id: str | None = None

    def __post_init__(self) -> None:
        if len(self.key) < AUDIT_KEY_MIN_BYTES:
            raise AuditKeyError("audit key must be at least 32 bytes")
        if self.key_id is None:
            digest = sha256(b"audit-key-id:v1\0" + self.key).hexdigest()[:16]
            object.__setattr__(self, "key_id", digest)
        elif not _safe_identifier(self.key_id):
            raise AuditKeyError("audit key_id is invalid")

    @classmethod
    def from_hex(cls, value: str, *, key_id: str | None = None) -> AuditKey:
        try:
            key = bytes.fromhex(value.strip())
        except ValueError as exc:
            raise AuditKeyError("audit key must be hex encoded") from exc
        return cls(key=key, key_id=key_id)

    def sender_ref_hash(self, normalized_sender_id: str) -> str:
        return self._reference("sender-ref:v1\0" + normalized_sender_id)

    def message_ref_hash(self, *, transport: str, room_id: str, message_id: str) -> str:
        return self._reference(f"message-ref:v1\0{transport}\0{room_id}\0{message_id}")

    def _reference(self, material: str) -> str:
        digest = hmac.new(self.key, material.encode("utf-8"), sha256).hexdigest()
        return f"hmac-sha256:v1:{self.key_id}:{digest}"


@dataclass(frozen=True, slots=True)
class NormalizedAuditSettings:
    enabled: bool
    audit_key: AuditKey | None = None
    warn_disabled: bool = False

    @classmethod
    def legacy_disabled(cls) -> NormalizedAuditSettings:
        return cls(enabled=False, audit_key=None, warn_disabled=True)

    @classmethod
    def standalone(
        cls,
        *,
        key_hex: str | None = None,
        key_file: str | None = None,
        require_enabled: bool = False,
    ) -> NormalizedAuditSettings:
        if key_hex and key_file:
            raise AuditKeyError("AUDIT_KEY and AUDIT_KEY_FILE are mutually exclusive")
        if key_hex:
            return cls(enabled=True, audit_key=AuditKey.from_hex(key_hex))
        if key_file:
            return cls(enabled=True, audit_key=load_audit_key_file(key_file))
        if require_enabled:
            raise AuditKeyError("normalized audit requires AUDIT_KEY or an audit key file")
        return cls.legacy_disabled()

    @classmethod
    def from_environment(cls, *, require_enabled: bool = False) -> NormalizedAuditSettings:
        return cls.standalone(
            key_hex=os.getenv("AUDIT_KEY"),
            key_file=os.getenv("AUDIT_KEY_FILE"),
            require_enabled=require_enabled or _env_bool("NORMALIZED_AUDIT_REQUIRED"),
        )

    @classmethod
    def homeassistant_app(
        cls,
        *,
        key_path: str = APP_AUDIT_KEY_PATH,
    ) -> NormalizedAuditSettings:
        return cls(enabled=True, audit_key=load_or_create_app_audit_key(key_path))


@dataclass(frozen=True, slots=True)
class NormalizedAuditEvent:
    event_type: NormalizedAuditEventType
    correlation_id: str
    transport: str
    source_room_id: str
    source_room_kind: str
    sender_stable: bool
    event_id: str = field(default_factory=new_event_id)
    causation_event_id: str | None = None
    reply_target_transport: str | None = None
    reply_target_room_id: str | None = None
    reply_target_room_kind: str | None = None
    sender_ref_hash: str | None = None
    sender_identity_kind: str | None = None
    message_ref_hash: str | None = None
    command_name: str | None = None
    command_result: str | None = None
    duration_ms: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = NORMALIZED_AUDIT_SCHEMA_VERSION

    @classmethod
    def from_inbound(
        cls,
        *,
        event_type: NormalizedAuditEventType,
        message: InboundMessage,
        audit_key: AuditKey,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        command_name: str | None = None,
        command_result: str | None = None,
        duration_ms: int | None = None,
        causation_event_id: str | None = None,
    ) -> NormalizedAuditEvent:
        source_room = _required_room(message.source_room)
        reply_target = message.reply_target
        sender = message.sender
        message_identity = message.message
        resolved_correlation_id = correlation_id or (
            message_identity.correlation_id if message_identity is not None else None
        )
        if resolved_correlation_id is None:
            raise ValueError(
                "normalized audit requires MessageIdentity or explicit correlation_id"
            )
        platform_message_id = (
            message_identity.message_id if message_identity is not None else message.message_id
        )
        return cls(
            event_type=event_type,
            correlation_id=resolved_correlation_id,
            causation_event_id=causation_event_id,
            transport=source_room.transport,
            source_room_id=source_room.room_id,
            source_room_kind=source_room.room_kind,
            reply_target_transport=reply_target.transport if reply_target else None,
            reply_target_room_id=reply_target.room_id if reply_target else None,
            reply_target_room_kind=reply_target.room_kind if reply_target else None,
            sender_ref_hash=(
                audit_key.sender_ref_hash(sender.sender_id) if sender is not None else None
            ),
            sender_identity_kind=sender.identity_kind if sender else None,
            sender_stable=sender.stable if sender else False,
            message_ref_hash=(
                audit_key.message_ref_hash(
                    transport=source_room.transport,
                    room_id=source_room.room_id,
                    message_id=platform_message_id,
                )
                if platform_message_id
                else None
            ),
            command_name=command_name,
            command_result=command_result,
            duration_ms=duration_ms,
            metadata=metadata or {},
            occurred_at=message.received_at,
        )

    def metadata_json(self) -> str:
        return canonical_metadata_json(self.event_type, self.metadata)

    def occurred_at_text(self) -> str:
        return utc_rfc3339(self.occurred_at)


class NormalizedAuditRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        settings: NormalizedAuditSettings,
    ) -> None:
        self.connection = connection
        self.settings = settings
        self._disabled_warning_emitted = False

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def warn_if_disabled(self) -> None:
        if self.settings.enabled:
            return
        if self.settings.warn_disabled and not self._disabled_warning_emitted:
            logger.warning("Normalized audit disabled: no audit key configured")
        self._disabled_warning_emitted = True

    def event_from_inbound(
        self,
        *,
        event_type: NormalizedAuditEventType,
        message: InboundMessage,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        command_name: str | None = None,
        command_result: str | None = None,
        duration_ms: int | None = None,
        causation_event_id: str | None = None,
    ) -> NormalizedAuditEvent:
        if self.settings.audit_key is None:
            raise AuditKeyError("normalized audit event creation requires an audit key")
        return NormalizedAuditEvent.from_inbound(
            event_type=event_type,
            message=message,
            audit_key=self.settings.audit_key,
            correlation_id=correlation_id,
            metadata=metadata,
            command_name=command_name,
            command_result=command_result,
            duration_ms=duration_ms,
            causation_event_id=causation_event_id,
        )

    def record(self, event: NormalizedAuditEvent) -> bool:
        if not self.settings.enabled:
            self.warn_if_disabled()
            return False
        if self.settings.audit_key is None:
            raise AuditKeyError("normalized audit is enabled without an audit key")
        self._validate_event(event)
        metadata_json = event.metadata_json()
        created_at = utc_rfc3339(datetime.now(UTC))
        write_transaction(
            self.connection,
            lambda: self.insert_event(
                event,
                metadata_json=metadata_json,
                created_at=created_at,
            ),
        )
        return True

    def insert_event(
        self,
        event: NormalizedAuditEvent,
        *,
        metadata_json: str | None = None,
        created_at: str | None = None,
    ) -> bool:
        if not self.settings.enabled:
            self.warn_if_disabled()
            return False
        if self.settings.audit_key is None:
            raise AuditKeyError("normalized audit is enabled without an audit key")
        self._validate_event(event)
        resolved_metadata_json = metadata_json or event.metadata_json()
        resolved_created_at = created_at or utc_rfc3339(datetime.now(UTC))
        self._record_metadata(
            "normalized_audit_schema_version",
            str(NORMALIZED_AUDIT_SCHEMA_VERSION),
            resolved_created_at,
        )
        self._record_metadata(
            "audit_key_id",
            self.settings.audit_key.key_id or "",
            resolved_created_at,
        )
        self.connection.execute(
            """
            INSERT INTO normalized_audit_events (
              event_id,
              schema_version,
              event_type,
              correlation_id,
              causation_event_id,
              transport,
              source_room_id,
              source_room_kind,
              reply_target_transport,
              reply_target_room_id,
              reply_target_room_kind,
              sender_ref_hash,
              sender_identity_kind,
              sender_stable,
              message_ref_hash,
              command_name,
              command_result,
              duration_ms,
              metadata_json,
              occurred_at,
              created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.schema_version,
                event.event_type.value,
                event.correlation_id,
                event.causation_event_id,
                event.transport,
                event.source_room_id,
                event.source_room_kind,
                event.reply_target_transport,
                event.reply_target_room_id,
                event.reply_target_room_kind,
                event.sender_ref_hash,
                event.sender_identity_kind,
                1 if event.sender_stable else 0,
                event.message_ref_hash,
                event.command_name,
                event.command_result,
                event.duration_ms,
                resolved_metadata_json,
                event.occurred_at_text(),
                resolved_created_at,
            ),
        )
        return True

    def _validate_event(self, event: NormalizedAuditEvent) -> None:
        validate_event_identity(event)
        expected_key_id = self.settings.audit_key.key_id if self.settings.audit_key else None
        if expected_key_id is None:
            raise AuditKeyError("normalized audit is enabled without an audit key")
        _validate_reference_key_id(event.sender_ref_hash, expected_key_id, "sender_ref_hash")
        _validate_reference_key_id(event.message_ref_hash, expected_key_id, "message_ref_hash")

    def _record_metadata(self, key: str, value: str, updated_at: str) -> None:
        self.connection.execute(
            """
            INSERT INTO audit_metadata (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value,
              updated_at = excluded.updated_at
            """,
            (key, value, updated_at),
        )


def utc_rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    value = value.astimezone(UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_event_identity(event: NormalizedAuditEvent) -> None:
    if event.schema_version != NORMALIZED_AUDIT_SCHEMA_VERSION:
        raise ValueError("normalized audit schema_version is unsupported")
    _validate_pattern(event.event_id, EVENT_ID_PATTERN, "event_id")
    _validate_pattern(event.correlation_id, CORRELATION_ID_PATTERN, "correlation_id")
    if event.causation_event_id is not None:
        _validate_pattern(
            event.causation_event_id,
            EVENT_ID_PATTERN,
            "causation_event_id",
        )
    if event.duration_ms is not None and event.duration_ms < 0:
        raise ValueError("duration_ms must be greater than or equal to zero")
    _validate_required_text(event.transport, "transport")
    _validate_required_text(event.source_room_id, "source_room_id")
    _validate_required_text(event.source_room_kind, "source_room_kind")
    if event.reply_target_transport is not None:
        _validate_required_text(event.reply_target_transport, "reply_target_transport")
    if event.reply_target_room_id is not None:
        _validate_required_text(event.reply_target_room_id, "reply_target_room_id")
    if event.reply_target_room_kind is not None:
        _validate_required_text(event.reply_target_room_kind, "reply_target_room_kind")
    if event.sender_ref_hash is not None:
        _validate_reference_format(event.sender_ref_hash, "sender_ref_hash")
    if event.message_ref_hash is not None:
        _validate_reference_format(event.message_ref_hash, "message_ref_hash")
    event.metadata_json()


def canonical_metadata_json(
    event_type: NormalizedAuditEventType,
    metadata: Mapping[str, Any],
) -> str:
    validated = validate_metadata(event_type, metadata)
    return json.dumps(validated, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def validate_metadata(
    event_type: NormalizedAuditEventType,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    allowed = METADATA_ALLOWLIST[event_type]
    unknown = set(metadata) - allowed
    if unknown:
        raise ValueError(f"metadata contains unknown keys: {sorted(unknown)}")
    if event_type is NormalizedAuditEventType.COMMAND_PARSED:
        parse_result = metadata.get("parse_result")
        if parse_result not in COMMAND_PARSE_RESULTS:
            raise ValueError("metadata parse_result is invalid")
    _validate_metadata_value(metadata, depth=0)
    copied = json.loads(json.dumps(metadata, ensure_ascii=True))
    if not isinstance(copied, dict):
        raise ValueError("metadata must be a mapping")
    encoded = json.dumps(copied, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > AUDIT_METADATA_MAX_BYTES:
        raise ValueError("metadata_json exceeds maximum size")
    return copied


def load_audit_key_file(path: str) -> AuditKey:
    key_path = Path(path)
    fd = _open_no_follow(key_path)
    try:
        stat_result = os.fstat(fd)
        if not stat.S_ISREG(stat_result.st_mode):
            raise AuditKeyError("audit key path is not a regular file")
        mode = stat.S_IMODE(stat_result.st_mode)
        if mode & 0o077:
            raise AuditKeyError("audit key file permissions are too broad")
        if hasattr(os, "getuid") and stat_result.st_uid != os.getuid():
            raise AuditKeyError("audit key file owner is invalid")
        key = os.read(fd, AUDIT_KEY_MIN_BYTES * 4)
        if len(os.read(fd, 1)) != 0:
            raise AuditKeyError("audit key file is too large")
    finally:
        os.close(fd)
    return AuditKey(key=_decode_key_bytes(key))


def load_or_create_app_audit_key(path: str = APP_AUDIT_KEY_PATH) -> AuditKey:
    key_path = Path(path)
    try:
        return load_audit_key_file(str(key_path))
    except FileNotFoundError:
        return _create_app_audit_key(key_path)
    except AuditKeyError:
        raise
    except OSError as exc:
        raise AuditKeyError("failed to read audit key file") from exc


def _create_app_audit_key(path: Path) -> AuditKey:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(AUDIT_KEY_MIN_BYTES)
    tmp_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp_path, flags, 0o600)
    try:
        _write_all(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        # Hard-link publication is atomic and never overwrites an existing key. If
        # another process wins the race, we load and validate the published key.
        os.link(tmp_path, path)
    except FileExistsError:
        return load_audit_key_file(str(path))
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()
    _fsync_directory(path.parent)
    return AuditKey(key=key)


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise AuditKeyError("failed to write audit key file")
        offset += written


def _open_no_follow(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise AuditKeyError("failed to open audit key file safely") from exc


def _decode_key_bytes(raw: bytes) -> bytes:
    decoded: bytes | None = None
    try:
        text = raw.decode("ascii").strip()
        if (
            len(text) >= AUDIT_KEY_MIN_BYTES * 2
            and len(text) % 2 == 0
            and all(char in "0123456789abcdefABCDEF" for char in text)
        ):
            decoded = bytes.fromhex(text)
    except (UnicodeDecodeError, ValueError):
        decoded = None
    key = decoded if decoded is not None else raw
    if len(key) < AUDIT_KEY_MIN_BYTES:
        raise AuditKeyError("audit key must be at least 32 bytes")
    return key


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _validate_metadata_value(value: object, *, depth: int) -> None:
    if depth > AUDIT_METADATA_MAX_DEPTH:
        raise ValueError("metadata exceeds maximum depth")
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        if len(value) > AUDIT_METADATA_MAX_STRING_LENGTH:
            raise ValueError("metadata string exceeds maximum length")
        return
    if isinstance(value, Mapping):
        if len(value) > AUDIT_METADATA_MAX_ITEMS:
            raise ValueError("metadata object has too many keys")
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("metadata keys must be strings")
            if len(key) > AUDIT_METADATA_MAX_STRING_LENGTH:
                raise ValueError("metadata key exceeds maximum length")
            _validate_metadata_value(nested, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > AUDIT_METADATA_MAX_ITEMS:
            raise ValueError("metadata array has too many elements")
        for nested in value:
            _validate_metadata_value(nested, depth=depth + 1)
        return
    raise ValueError("metadata contains unsupported value type")


def _required_room(room: RoomRef | None) -> RoomRef:
    if room is None:
        raise ValueError("normalized audit requires source_room")
    return room


def _safe_identifier(value: str) -> bool:
    return bool(value) and all(char.isalnum() or char in {"-", "_"} for char in value)


def _env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_reference_key_id(
    value: str | None,
    expected_key_id: str,
    field_name: str,
) -> None:
    if value is None:
        return
    match = _validate_reference_format(value, field_name)
    if match.group(1) != expected_key_id:
        raise ValueError(f"{field_name} key_id does not match audit key")


def _validate_reference_format(value: str, field_name: str) -> re.Match[str]:
    match = AUDIT_REFERENCE_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"{field_name} has invalid audit reference format")
    return match


def _validate_pattern(value: str, pattern: re.Pattern[str], field_name: str) -> None:
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} has invalid format")


def _validate_required_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
