from __future__ import annotations

from typing import Any

from strategies.base import load_strategy_defaults

_orb_defaults_cache: dict[str, Any] | None = None


def _load_orb_defaults() -> dict[str, Any]:
    return load_strategy_defaults("orb")


def get_orb_defaults() -> dict[str, Any]:
    """Canonical ORB runtime defaults, loaded once from configs/orb_base.yaml (the single
    source). Consumers (strategy.get_defaults, the compiler, apply_exits) must read
    defaults from here rather than re-declaring literals, so there is one home per value."""
    global _orb_defaults_cache
    if _orb_defaults_cache is None:
        _orb_defaults_cache = _load_orb_defaults()
    return _orb_defaults_cache
