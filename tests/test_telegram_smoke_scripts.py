from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_telegram_smoke_scripts_are_executable_and_parse() -> None:
    for relative in (
        "scripts/prepare-local-telegram-pr23.sh",
        "scripts/remove-local-telegram-pr23.sh",
        "scripts/telegram-enroll.py",
        "scripts/telegram-enroll.sh",
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
    subprocess.run(["bash", "-n", str(ROOT / "scripts/telegram-enroll.sh")], check=True)
    subprocess.run(
        ["python3", "-m", "py_compile", str(ROOT / "scripts/telegram-enroll.py")],
        check=True,
    )


def test_telegram_enroll_does_not_accept_token_argument() -> None:
    content = (ROOT / "scripts/telegram-enroll.py").read_text(encoding="utf-8")

    assert "add_argument(\"--token" not in content
    assert "getpass.getpass" in content


def test_prepare_local_pr23_runs_without_python3(tmp_path: Path) -> None:
    source_repo = tmp_path / "source"
    addons_root = tmp_path / "addons"
    _create_fake_pr_repo(source_repo)
    expected_head = subprocess.check_output(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    path_without_python = _path_without_python(tmp_path)

    env = {
        **os.environ,
        "PATH": str(path_without_python),
        "MCB_PR23_TEST_ADDONS_ROOT": str(addons_root),
        "MCB_PR23_TEST_REPO_URL": str(source_repo),
    }
    script = ROOT / "scripts/prepare-local-telegram-pr23.sh"

    for _ in range(2):
        subprocess.run(["bash", str(script), expected_head], check=True, env=env)

    app_root = addons_root / "meshcore-control-bridge-pr23"
    config = app_root / "config.yaml"
    lines = config.read_text(encoding="utf-8").splitlines()

    assert (addons_root / ".meshcore-control-bridge-pr23-source/.git").is_dir()
    assert (app_root / "Dockerfile").is_file()
    assert (app_root / "run.sh").is_file()
    assert (app_root / "pyproject.toml").is_file()
    assert (app_root / "README.md").is_file()
    assert (app_root / "src").is_dir()
    assert not (app_root / "meshcore-control-bridge/config.yaml").exists()
    assert lines.count("name: MeshCore Control Bridge PR23") == 1
    assert lines.count("slug: meshcore_control_bridge_pr23") == 1
    assert 'image: "ghcr.io/j3udiel/meshcore-control-bridge"' not in lines
    assert lines.count('# image: "ghcr.io/j3udiel/meshcore-control-bridge"') == 1
    dockerfile = (app_root / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY meshcore-control-bridge/run.sh" not in dockerfile
    _assert_dockerfile_copy_sources_exist(app_root)
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    subprocess.run(
        ["docker", "build", "-t", "meshcore-control-bridge-pr23-context-test", str(app_root)],
        check=True,
    )


def test_shell_enroll_extracts_private_ids_without_python_or_payload_leak(tmp_path: Path) -> None:
    bin_dir = _path_without_python(tmp_path)
    fake_curl = bin_dir / "curl"
    calls = tmp_path / "curl-calls"
    private_update = (
        '{"ok":true,"result":[{"update_id":101,"message":{"message_id":7,'
        '"from":{"id":4242,"is_bot":false,"first_name":"Hidden"},'
        '"chat":{"id":4242,"type":"private","username":"hidden"},'
        '"date":1,"text":"private text"}}]}'
    )
    fake_curl.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                f"calls='{calls}'",
                "count=0",
                "[ -f \"$calls\" ] && read count < \"$calls\"",
                "count=$((count + 1))",
                "printf '%s' \"$count\" > \"$calls\"",
                "case \"$*\" in",
                "  *getMe*) printf '{\"ok\":true}\\n200' ;;",
                "  *getUpdates*)",
                "    if [ \"$count\" -eq 2 ]; then",
                "      printf '{\"ok\":true,\"result\":[]}\\n200'",
                "    else",
                f"      printf '{private_update}\\n200'",
                "    fi",
                "    ;;",
                "  *) printf '{\"ok\":false}\\n400' ;;",
                "esac",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "TELEGRAM_API_BASE_URL": "https://telegram.example.invalid",
    }
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/telegram-enroll.sh"), "--timeout", "3"],
        input="123456:SECRET_TOKEN\n",
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )

    assert result.stdout == 'allowed_private_chat_id: "4242"\nallowed_user_id: "4242"\n'
    combined_output = result.stdout + result.stderr
    assert "SECRET_TOKEN" not in combined_output
    assert "private text" not in combined_output
    assert "hidden" not in combined_output.lower()


def test_smoke_docs_do_not_pipe_remote_code_to_shell() -> None:
    docs = (ROOT / "meshcore-control-bridge/DOCS.md").read_text(encoding="utf-8")

    assert "curl |" not in docs
    assert "| bash" not in docs
    assert "bot_token_import: \"<token>\"" in docs


def _create_fake_pr_repo(path: Path) -> None:
    path.mkdir()
    app_dir = path / "meshcore-control-bridge"
    config = app_dir / "config.yaml"
    config.parent.mkdir(parents=True)
    (path / "src/meshcore_control").mkdir(parents=True)
    (path / "src/meshcore_control/__init__.py").write_text("", encoding="utf-8")
    (path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    (app_dir / "run.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (app_dir / "Dockerfile").write_text(
        "\n".join(
            [
                "FROM scratch",
                "COPY pyproject.toml README.md /app/package/",
                "COPY src /app/package/src",
                "COPY meshcore-control-bridge/run.sh /run.sh",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config.write_text(
        "\n".join(
            [
                "name: MeshCore Control Bridge",
                "slug: meshcore_control_bridge",
                'image: "ghcr.io/j3udiel/meshcore-control-bridge"',
                "version: 0.1.8",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(path), "init", "-b", "feat/telegram-readonly-commands"],
        check=True,
    )
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test User",
            "commit",
            "-m",
            "fixture",
        ],
        check=True,
    )


def _path_without_python(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in (
        "awk",
        "bash",
        "chmod",
        "cp",
        "git",
        "grep",
        "id",
        "mkdir",
        "mktemp",
        "mv",
        "sed",
    ):
        source = shutil.which(command)
        assert source is not None
        (bin_dir / command).symlink_to(source)
    assert not (bin_dir / "python3").exists()
    return bin_dir


def _assert_dockerfile_copy_sources_exist(app_root: Path) -> None:
    for line in (app_root / "Dockerfile").read_text(encoding="utf-8").splitlines():
        words = line.split()
        if not words or words[0] != "COPY":
            continue
        sources = words[1:-1]
        for source in sources:
            assert not source.startswith("--")
            assert (app_root / source).exists(), f"missing Docker COPY source: {source}"
