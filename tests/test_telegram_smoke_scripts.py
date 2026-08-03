from __future__ import annotations

import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_telegram_smoke_scripts_are_executable_and_parse() -> None:
    for relative in (
        "scripts/prepare-local-telegram-pr23.sh",
        "scripts/remove-local-telegram-pr23.sh",
        "scripts/telegram-enroll.py",
    ):
        path = ROOT / relative
        assert path.is_file()
        assert path.stat().st_mode & stat.S_IXUSR

    subprocess.run(
        ["bash", "-n", str(ROOT / "scripts/prepare-local-telegram-pr23.sh")],
        check=True,
    )
    subprocess.run(
        ["bash", "-n", str(ROOT / "scripts/remove-local-telegram-pr23.sh")],
        check=True,
    )
    subprocess.run(
        ["python3", "-m", "py_compile", str(ROOT / "scripts/telegram-enroll.py")],
        check=True,
    )


def test_telegram_enroll_does_not_accept_token_argument() -> None:
    content = (ROOT / "scripts/telegram-enroll.py").read_text(encoding="utf-8")

    assert "add_argument(\"--token" not in content
    assert "getpass.getpass" in content


def test_smoke_docs_do_not_pipe_remote_code_to_shell() -> None:
    docs = (ROOT / "meshcore-control-bridge/DOCS.md").read_text(encoding="utf-8")

    assert "curl |" not in docs
    assert "| bash" not in docs
    assert "bot_token_import: \"<token>\"" in docs
