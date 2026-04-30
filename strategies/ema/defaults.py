from __future__ import annotations

from typing import Any

from strategies.base import load_strategy_defaults

_ema_defaults_cache: dict[str, Any] | None = None


def _load_ema_defaults() -> dict[str, Any]:
    raw = load_strategy_defaults("ema")
    return {k: v for k, v in raw.items() if k != "family"}


def _get_ema_defaults() -> dict[str, Any]:
    global _ema_defaults_cache
    if _ema_defaults_cache is None:
        _ema_defaults_cache = _load_ema_defaults()
    return _ema_defaults_cache
