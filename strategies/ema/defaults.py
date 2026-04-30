from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ema_defaults_cache: dict[str, Any] | None = None


def _load_ema_defaults() -> dict[str, Any]:
    base_path = Path(__file__).resolve().parent / "defaults.yaml"
    raw = yaml.safe_load(base_path.read_text())
    return {k: v for k, v in raw.items() if k != "family"}


def _get_ema_defaults() -> dict[str, Any]:
    global _ema_defaults_cache
    if _ema_defaults_cache is None:
        _ema_defaults_cache = _load_ema_defaults()
    return _ema_defaults_cache
