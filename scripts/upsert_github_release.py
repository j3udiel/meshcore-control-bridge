#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_VERSION = "2022-11-28"


def is_prerelease_version(version: str) -> bool:
    return "-" in version


def build_release_payload(
    *,
    tag_name: str,
    title: str,
    body: str,
    prerelease: bool,
) -> dict[str, Any]:
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-.A-Za-z0-9]+)?", tag_name):
        raise ValueError("tag_name must look like vX.Y.Z")
    if not title.strip():
        raise ValueError("title must not be empty")
    if not body.strip():
        raise ValueError("body must not be empty")
    return {
        "tag_name": tag_name,
        "name": title,
        "body": body,
        "draft": False,
        "prerelease": prerelease,
    }


class GitHubReleaseClient:
    def __init__(self, *, repository: str, token: str) -> None:
        if "/" not in repository:
            raise ValueError("repository must be owner/name")
        if not token:
            raise ValueError("GitHub token is required")
        self._repository = repository
        self._token = token

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self._repository}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RuntimeError(f"GitHub API request failed with HTTP {exc.code}") from exc

    def get_by_tag(self, tag_name: str) -> dict[str, Any] | None:
        result = self._request("GET", f"/releases/tags/{tag_name}")
        return result if isinstance(result, dict) else None

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request("POST", "/releases", payload)
        if not isinstance(result, dict):
            raise RuntimeError("GitHub API did not return a release object")
        return result

    def update(self, release_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request("PATCH", f"/releases/{release_id}", payload)
        if not isinstance(result, dict):
            raise RuntimeError("GitHub API did not return a release object")
        return result

    def upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_by_tag(str(payload["tag_name"]))
        if existing is None:
            return self.create(payload)
        release_id = existing.get("id")
        if not isinstance(release_id, int):
            raise RuntimeError("existing release is missing a numeric id")
        return self.update(release_id, payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--notes-file", required=True)
    parser.add_argument("--prerelease", choices=["true", "false"], required=True)
    args = parser.parse_args()

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    body = Path(args.notes_file).read_text(encoding="utf-8")
    payload = build_release_payload(
        tag_name=args.tag,
        title=args.title,
        body=body,
        prerelease=args.prerelease == "true",
    )
    release = GitHubReleaseClient(repository=repository, token=token).upsert(payload)
    print(release.get("html_url", "release-upserted"))


if __name__ == "__main__":
    main()
