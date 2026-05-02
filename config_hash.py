from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any


def _config_hash(config: dict[str, Any]) -> str:
    """Deterministic hash of a config dict."""
    blob = json.dumps(_normalize_config_for_hash(config), sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def _normalize_config_for_hash(config: dict[str, Any]) -> dict[str, Any]:
    if not config.get("data_universe"):
        return dict(config)
    normalized = dict(config)
    provenance = normalized.get("data_provenance")
    if isinstance(provenance, dict):
        normalized["data_provenance"] = {
            key: value
            for key, value in provenance.items()
            if key not in {"universe_path", "manifest_path"}
        }
    return normalized


def _git_sha() -> str:
    """Current git SHA, or 'unknown' if not in a repo."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"
