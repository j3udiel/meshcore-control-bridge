from __future__ import annotations

import logging
import os
import re
from typing import Any

_REDACTED = "[REDACTED]"
_ACCESS_TOKEN_RE = re.compile(r'("access_token"\s*:\s*")[^"]+(")', re.IGNORECASE)
_ACCESS_TOKEN_TEXT_RE = re.compile(r"(access_token\s*[=:]\s*)\S+", re.IGNORECASE)
_AUTHORIZATION_RE = re.compile(r"(Authorization\s*:\s*Bearer\s+)[^\s,;]+", re.IGNORECASE)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
_SUPERVISOR_RE = re.compile(r"(SUPERVISOR_TOKEN\s*[=:]\s*)\S+", re.IGNORECASE)

_NOISY_LOGGERS = (
    "websockets.client",
    "websockets.server",
    "urllib3.connectionpool",
    "httpx",
    "httpcore",
)


class SecretRedactionFilter(logging.Filter):
    def __init__(self, known_secrets: list[str] | None = None) -> None:
        super().__init__()
        self.known_secrets = [secret for secret in known_secrets or [] if secret]

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            try:
                record.msg = self._sanitize_text(record.getMessage())
                record.args = ()
            except Exception:
                record.msg = self._sanitize(record.msg)
                record.args = self._sanitize(record.args)
        else:
            record.msg = self._sanitize(record.msg)
        if record.exc_text:
            record.exc_text = self._sanitize(record.exc_text)
        return True

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._sanitize_text(value)
        if isinstance(value, tuple):
            return tuple(self._sanitize(item) for item in value)
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, dict):
            return {key: self._sanitize(item) for key, item in value.items()}
        return value

    def _sanitize_text(self, value: str) -> str:
        sanitized = _ACCESS_TOKEN_RE.sub(rf"\1{_REDACTED}\2", value)
        sanitized = _ACCESS_TOKEN_TEXT_RE.sub(rf"\1{_REDACTED}", sanitized)
        sanitized = _AUTHORIZATION_RE.sub(rf"\1{_REDACTED}", sanitized)
        sanitized = _BEARER_RE.sub(rf"\1{_REDACTED}", sanitized)
        sanitized = _SUPERVISOR_RE.sub(rf"\1{_REDACTED}", sanitized)
        for secret in self.known_secrets:
            sanitized = sanitized.replace(secret, _REDACTED)
            if len(secret) >= 12:
                sanitized = sanitized.replace(secret[:8], _REDACTED)
                sanitized = sanitized.replace(secret[-8:], _REDACTED)
        return sanitized


def configure_logging(level: str = "INFO") -> None:
    known_secrets = [os.getenv("SUPERVISOR_TOKEN", "")]
    redaction_filter = SecretRedactionFilter(known_secrets)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    root = logging.getLogger()
    root.addFilter(redaction_filter)
    for handler in root.handlers:
        handler.addFilter(redaction_filter)
    for logger_name in _NOISY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
