#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


def extract_changelog_section(changelog: str, version: str) -> str:
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\](?:[^\n]*)\n(?P<body>.*?)(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md does not contain a section for {version}")
    body = match.group("body").strip()
    if not body:
        raise ValueError(f"CHANGELOG.md section for {version} is empty")
    return body


def build_release_notes(
    *,
    version: str,
    changelog: str,
    image: str,
    digest: str,
) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-.A-Za-z0-9]+)?", version):
        raise ValueError("version must be a release version without the leading v")
    if not digest.startswith("sha256:") or len(digest) != len("sha256:") + 64:
        raise ValueError("digest must be a sha256 digest")

    body = extract_changelog_section(changelog, version)
    return (
        f"## MeshCore Control Bridge {version}\n\n"
        f"{body}\n\n"
        "### Container image\n\n"
        f"`{image}:{version}`\n\n"
        "Multiarch digest:\n\n"
        f"`{digest}`\n\n"
        "Platforms:\n\n"
        "- `linux/amd64`\n"
        "- `linux/arm64`\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--changelog", default="CHANGELOG.md")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    changelog = Path(args.changelog).read_text(encoding="utf-8")
    notes = build_release_notes(
        version=args.version,
        changelog=changelog,
        image=args.image,
        digest=args.digest,
    )
    Path(args.output).write_text(notes, encoding="utf-8")


if __name__ == "__main__":
    main()
