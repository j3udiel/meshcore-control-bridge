from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-secrets.sh"


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CHECK_SECRETS_ROOT"] = str(root)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_check_secrets_accepts_placeholders(tmp_path) -> None:
    (tmp_path / ".env.example").write_text(
        "HA_TOKEN=replace-with-home-assistant-long-lived-access-token\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "Use HA_TOKEN=replace-with-home-assistant-long-lived-access-token\n",
        encoding="utf-8",
    )

    result = run_check(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""


def test_check_secrets_detects_realistic_fake_credential(tmp_path) -> None:
    fake_token = "Bear" + "er " + "abcdefghijklmnopqrstuvwxyz1234567890"
    (tmp_path / "config.py").write_text(
        f'TOKEN = "{fake_token}"\n',
        encoding="utf-8",
    )

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "config.py:1: possible Bearer token" in result.stdout
    assert "abcdefghijklmnopqrstuvwxyz" not in result.stdout
