from __future__ import annotations

from typing import Any

from strategies.base import load_strategy_defaults

_orb_defaults_cache: dict[str, Any] | None = None


def _load_orb_defaults() -> dict[str, Any]:
    return load_strategy_defaults("orb")


def _get_orb_defaults() -> dict[str, Any]:
    global _orb_defaults_cache
    if _orb_defaults_cache is None:
        _orb_defaults_cache = _load_orb_defaults()
    return _orb_defaults_cache
