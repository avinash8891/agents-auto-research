from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any


def _config_hash(config: dict[str, Any]) -> str:
    """Deterministic hash of a config dict."""
    blob = json.dumps(config, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


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
