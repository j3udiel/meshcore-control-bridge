from __future__ import annotations

import io
import logging

from meshcore_control.logging import SecretRedactionFilter, configure_logging


def test_configure_logging_keeps_protocol_loggers_at_warning() -> None:
    configure_logging("DEBUG")

    assert logging.getLogger("websockets.client").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("websockets.server").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("urllib3.connectionpool").getEffectiveLevel() == logging.WARNING


def test_secret_redaction_filter_removes_token_fragments_from_debug_logs() -> None:
    token = "supervisor-token-abcdefghijklmnopqrstuvwxyz-1234567890"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretRedactionFilter([token]))
    logger = logging.getLogger("test.secret-redaction")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    logger.debug('TEXT \'{"type": "auth", "access_token": "%s"}\'', token)
    logger.debug("Authorization: Bearer %s", token)
    logger.debug("SUPERVISOR_TOKEN=%s", token)

    output = stream.getvalue()

    assert token not in output
    assert token[:8] not in output
    assert token[-8:] not in output
    assert "[REDACTED]" in output
