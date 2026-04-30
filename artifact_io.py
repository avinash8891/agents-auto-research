from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from persistence_utils import utc_now_iso8601, write_json_atomic


def read_json_artifacts(directory: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    if not directory.exists():
        return artifacts
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        payload["artifact_path"] = path.as_posix()
        artifacts.append(payload)
    return artifacts


def write_json_artifact(path: Path, payload: dict[str, Any]) -> Path:
    write_json_atomic(path, payload)
    return path


def timestamp_now() -> str:
    return utc_now_iso8601()
