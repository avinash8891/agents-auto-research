from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from autoresearch_constants import MILLISECONDS_PER_SECOND


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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp_path, path)
    return path


def timestamp_ms() -> int:
    return int(time.time() * MILLISECONDS_PER_SECOND)
