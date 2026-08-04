from scripts.build_release_notes import build_release_notes, extract_changelog_section
from scripts.upsert_github_release import build_release_payload, is_prerelease_version


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
