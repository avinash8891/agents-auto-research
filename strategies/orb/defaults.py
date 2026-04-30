from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_orb_defaults_cache: dict[str, Any] | None = None


def _load_orb_defaults() -> dict[str, Any]:
    base_path = Path(__file__).resolve().parents[2] / "configs" / "orb_base.yaml"
    return yaml.safe_load(base_path.read_text())


def _get_orb_defaults() -> dict[str, Any]:
    global _orb_defaults_cache
    if _orb_defaults_cache is None:
        _orb_defaults_cache = _load_orb_defaults()
    return _orb_defaults_cache
