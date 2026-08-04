from scripts.build_release_notes import build_release_notes, extract_changelog_section


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
