from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from meshcore_control.homeassistant_app import APP_HEALTHCHECK_PATH


def main() -> None:
    path = Path(APP_HEALTHCHECK_PATH)
    if not path.exists():
        raise SystemExit("healthcheck file does not exist")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("status") not in {"ok", "degraded"}:
        raise SystemExit("healthcheck status is not ok or degraded")
    updated_at = payload.get("updated_at")
    if not isinstance(updated_at, str):
        raise SystemExit("healthcheck timestamp is missing")
    parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - parsed).total_seconds()
    if age > 300:
        raise SystemExit("healthcheck is stale")


if __name__ == "__main__":
    main()
