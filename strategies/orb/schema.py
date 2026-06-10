from __future__ import annotations

from typing import Any

CANONICAL_REGIMES: tuple[str, ...] = (
    "chop",
    "high-vol",
    "low-vol",
    "trend",
    "wide-OR",
    "narrow-OR",
)

REGIME_ALIASES: dict[str, str] = {
    "chop": "chop",
    "high-vol": "high-vol",
    "highvol": "high-vol",
    "low-vol": "low-vol",
    "lowvol": "low-vol",
    "trend": "trend",
    "trend-up": "trend",
    "trend-down": "trend",
    "wide-OR": "wide-OR",
    "wide-or": "wide-OR",
    "wideOR": "wide-OR",
    "narrow-OR": "narrow-OR",
    "narrow-or": "narrow-OR",
    "narrowOR": "narrow-OR",
}

SUPPORTED_CONFIG_KEYS: tuple[str, ...] = (
    "data_universe",
    "data_provenance",
    "symbols",
    "discovery_start",
    "discovery_end",
    "validation_start",
    "validation_end",
    "holdout_start",
    "holdout_end",
    "universe_mode",
    "stocks_in_play_top_n",
    "stocks_in_play_ranking",
    "or_minutes",
    "timeframe_minutes",
    "rr_ratio",
    "max_one_entry_per_day",
    "long_only",
    "use_volume_filter",
    "use_trend_filter",
    "use_wide_or_filter",
    "use_follow_through",
    "wide_or_mult",
    "narrow_or_mult",
    "trend_ema_period",
    "volume_lookback",
    "max_hold_bars",
    "slippage_pct",
    "use_time_stop",
    "time_stop_hour",
    "time_stop_minute",
    "use_failed_breakout_exit",
    "use_volatility_trail",
    "vol_trail_atr_mult",
    "use_opposite_break_exit",
    "conservative_sl_fill",
    "skip_regimes",
    "require_regimes",
    "use_rvol_gate",
    "rvol_lookback_days",
    "rvol_threshold",
    "rvol_baseline_stat",
    "rvol_min_history_days",
    "rvol_computation",
    "outside_or_bar_policy",
    "_research_source",
    "_combination",
)


def canonical_regimes() -> tuple[str, ...]:
    return CANONICAL_REGIMES


def supported_config_keys() -> tuple[str, ...]:
    return SUPPORTED_CONFIG_KEYS


def normalize_regime_name(name: str) -> str:
    try:
        return REGIME_ALIASES[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported regime value: {name}") from exc


def _normalize_regime_list(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        canonical = normalize_regime_name(str(value))
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized


def normalize_config_changes(config_changes: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(config_changes) - set(SUPPORTED_CONFIG_KEYS))
    if unknown:
        raise ValueError(f"Unsupported config keys: {', '.join(unknown)}")

    normalized = dict(config_changes)
    for key in ("skip_regimes", "require_regimes"):
        if key in normalized and normalized[key] is not None:
            normalized[key] = _normalize_regime_list(list(normalized[key]))
    return normalized
