from __future__ import annotations

import os
import re
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

TOKEN_PATTERN = re.compile(r"^[0-9]{6,}:[A-Za-z0-9_-]{20,}$")


class TelegramTokenError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TelegramToken:
    value: str

    def __repr__(self) -> str:
        return "TelegramToken(value='[REDACTED]')"

    def __str__(self) -> str:
        return "[REDACTED]"


def load_or_import_token(*, token_import: str, token_file: str) -> TelegramToken:
    path = Path(token_file)
    if token_import:
        token = validate_bot_token(token_import)
        _write_token_atomic(path, token)
        return TelegramToken(token)
    return TelegramToken(_read_token_file(path))


def validate_bot_token(value: str) -> str:
    token = value.strip()
    if not TOKEN_PATTERN.fullmatch(token):
        raise TelegramTokenError("Telegram bot token format is invalid")
    return token


def _read_token_file(path: Path) -> str:
    fd = _open_regular_no_follow(path, write=False)
    try:
        stat_result = os.fstat(fd)
        _validate_token_file_stat(stat_result)
        raw = os.read(fd, 4096)
        if len(os.read(fd, 1)) != 0:
            raise TelegramTokenError("Telegram bot token file is too large")
    finally:
        os.close(fd)
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise TelegramTokenError("Telegram bot token file is not UTF-8") from exc
    return validate_bot_token(token)


def _write_token_atomic(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        fd = _open_regular_no_follow(path, write=False)
        try:
            _validate_token_file_stat(os.fstat(fd))
        finally:
            os.close(fd)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp_path, flags, 0o600)
    try:
        _write_all(fd, (token + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def _open_regular_no_follow(path: Path, *, write: bool) -> int:
    flags = os.O_RDWR if write else os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise TelegramTokenError("Telegram bot token file does not exist") from None
    except OSError as exc:
        raise TelegramTokenError("failed to open Telegram bot token file safely") from exc
    stat_result = os.fstat(fd)
    if not stat.S_ISREG(stat_result.st_mode):
        os.close(fd)
        raise TelegramTokenError("Telegram bot token file is not a regular file")
    return fd


def _validate_token_file_stat(stat_result: os.stat_result) -> None:
    if not stat.S_ISREG(stat_result.st_mode):
        raise TelegramTokenError("Telegram bot token file is not a regular file")
    if stat.S_IMODE(stat_result.st_mode) & 0o077:
        raise TelegramTokenError("Telegram bot token file permissions are too broad")
    if hasattr(os, "getuid") and stat_result.st_uid != os.getuid():
        raise TelegramTokenError("Telegram bot token file owner is invalid")


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise TelegramTokenError("failed to write Telegram bot token file")
        offset += written


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
