from __future__ import annotations

from pathlib import Path
from typing import Any


def _load_ema_defaults() -> dict[str, Any]:
    """Load EMA baseline defaults from ema_base.yaml (single source of truth).

    Returns ALL keys from the yaml except metadata keys. This means adding
    a new parameter to ema_base.yaml automatically makes it available for
    thesis compilation — no whitelist to keep in sync.
    """
    import yaml

    base_path = Path(__file__).resolve().parent / "configs" / "ema_base.yaml"
    with open(base_path) as f:
        raw = yaml.safe_load(f)
    return {k: v for k, v in raw.items() if k != "family"}


# Loaded lazily from configs/ema_base.yaml — single source of truth
_ema_defaults_cache: dict[str, Any] | None = None
_orb_defaults_cache: dict[str, Any] | None = None


# Keys in orb_base.yaml that are metadata, not backtest parameters.
_ORB_META_KEYS: set[str] = set()


def _load_orb_defaults() -> dict[str, Any]:
    """Load ORB baseline defaults from orb_base.yaml (single source of truth).

    Returns ALL keys from the yaml except metadata keys.
    """
    import yaml

    base_path = Path(__file__).resolve().parent / "configs" / "orb_base.yaml"
    with open(base_path) as f:
        raw = yaml.safe_load(f)
    return {k: v for k, v in raw.items() if k not in _ORB_META_KEYS}


def _get_orb_defaults() -> dict[str, Any]:
    global _orb_defaults_cache
    if _orb_defaults_cache is None:
        _orb_defaults_cache = _load_orb_defaults()
    return _orb_defaults_cache


def _get_ema_defaults() -> dict[str, Any]:
    global _ema_defaults_cache
    if _ema_defaults_cache is None:
        _ema_defaults_cache = _load_ema_defaults()
    return _ema_defaults_cache
