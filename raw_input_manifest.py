from __future__ import annotations

import json
from pathlib import Path


class RawInputManifestError(RuntimeError):
    pass


def raw_input_manifest_path(root: Path) -> Path:
    return root / "runtime" / "raw_input_manifest.json"


def available_raw_inputs(root: Path) -> frozenset[str]:
    path = raw_input_manifest_path(root)
    if not path.exists():
        raise RawInputManifestError(f"missing {path.relative_to(root)}")
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RawInputManifestError(f"invalid JSON in {path.relative_to(root)}: {exc}") from exc
    values = payload.get("available_raw_inputs")
    if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
        raise RawInputManifestError("available_raw_inputs must be a list of non-empty strings")
    return frozenset(values)
