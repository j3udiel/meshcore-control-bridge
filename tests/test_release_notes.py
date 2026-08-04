from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.build_release_notes import build_release_notes, extract_changelog_section
from scripts.upsert_github_release import (
    GitHubReleaseClient,
    build_release_payload,
    is_prerelease_version,
)

ROOT = Path(__file__).resolve().parents[1]


def test_extract_changelog_section() -> None:
    changelog = """# Changelog

## [Unreleased]

## [0.1.9] - 2026-08-04

### Added

- One.

## [0.1.8] - 2026-08-03

- Previous.
"""

    assert extract_changelog_section(changelog, "0.1.9") == "### Added\n\n- One."


def test_build_release_notes_includes_image_digest_and_platforms() -> None:
    changelog = """# Changelog

## [0.1.9] - 2026-08-04

### Added

- One.
"""
    digest = "sha256:" + ("a" * 64)

    notes = build_release_notes(
        version="0.1.9",
        changelog=changelog,
        image="ghcr.io/example/project",
        digest=digest,
    )

    assert notes.startswith("## MeshCore Control Bridge 0.1.9\n")
    assert "### Added\n\n- One." in notes
    assert "`ghcr.io/example/project:0.1.9`" in notes
    assert f"`{digest}`" in notes
    assert "- `linux/amd64`" in notes
    assert "- `linux/arm64`" in notes
    assert notes.count("```") == 0


def test_release_payload_is_not_draft_and_uses_existing_tag() -> None:
    payload = build_release_payload(
        tag_name="v0.1.9",
        title="MeshCore Control Bridge 0.1.9",
        body="notes",
        prerelease=False,
    )

    assert payload == {
        "tag_name": "v0.1.9",
        "name": "MeshCore Control Bridge 0.1.9",
        "body": "notes",
        "draft": False,
        "prerelease": False,
    }


def test_prerelease_detection() -> None:
    assert is_prerelease_version("0.1.10-beta.1") is True
    assert is_prerelease_version("0.1.10") is False


def test_release_payload_rejects_invalid_or_empty_values() -> None:
    with pytest.raises(ValueError, match="tag_name"):
        build_release_payload(
            tag_name="0.1.9",
            title="MeshCore Control Bridge 0.1.9",
            body="notes",
            prerelease=False,
        )
    with pytest.raises(ValueError, match="title"):
        build_release_payload(tag_name="v0.1.9", title="", body="notes", prerelease=False)
    with pytest.raises(ValueError, match="body"):
        build_release_payload(tag_name="v0.1.9", title="Title", body="", prerelease=False)


class FakeReleaseClient(GitHubReleaseClient):
    def __init__(self, existing: dict[str, Any] | None) -> None:
        self.existing = existing
        self.created: dict[str, Any] | None = None
        self.updated: tuple[int, dict[str, Any]] | None = None

    def get_by_tag(self, tag_name: str) -> dict[str, Any] | None:
        assert tag_name == "v0.1.9"
        return self.existing

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.created = payload
        return {"id": 101, "html_url": "https://example.invalid/releases/101", **payload}

    def update(self, release_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.updated = (release_id, payload)
        return {"id": release_id, "html_url": "https://example.invalid/releases/101", **payload}


def test_release_upsert_creates_missing_release() -> None:
    payload = build_release_payload(
        tag_name="v0.1.9",
        title="MeshCore Control Bridge 0.1.9",
        body="notes",
        prerelease=False,
    )
    client = FakeReleaseClient(existing=None)

    release = client.upsert(payload)

    assert client.created == payload
    assert client.updated is None
    assert release["id"] == 101


def test_release_upsert_updates_existing_release_without_duplicate() -> None:
    payload = build_release_payload(
        tag_name="v0.1.9",
        title="MeshCore Control Bridge 0.1.9",
        body="notes",
        prerelease=False,
    )
    client = FakeReleaseClient(existing={"id": 101})

    release = client.upsert(payload)

    assert client.created is None
    assert client.updated == (101, payload)
    assert release["id"] == 101


def test_publish_workflow_release_permissions_are_minimal() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/publish-home-assistant-app.yml").read_text()
    )

    assert workflow["jobs"]["build"]["permissions"]["packages"] == "write"
    assert workflow["jobs"]["manifest"]["permissions"]["packages"] == "write"
    assert workflow["jobs"]["release"]["permissions"] == {
        "contents": "write",
        "packages": "read",
    }


def test_release_only_workflow_is_manual_and_does_not_publish_images() -> None:
    path = ROOT / ".github/workflows/upsert-github-release.yml"
    workflow = yaml.safe_load(path.read_text())
    text = path.read_text()

    assert "workflow_dispatch" in workflow[True]
    assert workflow["permissions"] == {"contents": "write", "packages": "read"}
    assert "docker build " not in text
    assert "publish-multi-arch-manifest" not in text
    assert "build-image" not in text
    assert "git show \"${tag_commit}:CHANGELOG.md\" > /tmp/tag-CHANGELOG.md" in text
    assert "git merge-base --is-ancestor" in text
    assert "git cat-file -t \"${TAG_NAME}\"" in text
    assert "Platform:    linux/amd64" in text
    assert "Platform:    linux/arm64" in text
