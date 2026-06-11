from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

FamilyFeatureExtractor = Callable[
    [pd.DataFrame, pd.DataFrame, pd.Timestamp, float, dict[str, Any]],
    dict[str, float],
]

FAMILY_ENTRY_FEATURE_COLUMNS = ("or_width_pctile", "dist_to_ema_pct")


def family_entry_features(
    family: str,
    *,
    symbol_bars: pd.DataFrame,
    prior_bars: pd.DataFrame,
    entry_ts: pd.Timestamp,
    entry_price: float,
    runtime_config: dict[str, Any],
) -> dict[str, float]:
    features: dict[str, float] = {column: np.nan for column in FAMILY_ENTRY_FEATURE_COLUMNS}
    extractor = _EXTRACTORS.get(str(family).lower())
    if extractor is None:
        return features
    features.update(extractor(symbol_bars, prior_bars, entry_ts, entry_price, runtime_config))
    return features


def _ema_entry_features(
    symbol_bars: pd.DataFrame,
    prior_bars: pd.DataFrame,
    entry_ts: pd.Timestamp,
    entry_price: float,
    runtime_config: dict[str, Any],
) -> dict[str, float]:
    del symbol_bars, entry_ts
    if prior_bars.empty or not np.isfinite(entry_price) or entry_price == 0:
        return {}
    ema_length = _int_or_default(runtime_config.get("ema_length", 5), 5)
    if ema_length <= 0:
        return {}
    ema = prior_bars["close"].astype(float).ewm(span=ema_length, adjust=False).mean().iloc[-1]
    return {"dist_to_ema_pct": float((entry_price - ema) / entry_price * 100.0)}


def _orb_entry_features(
    symbol_bars: pd.DataFrame,
    prior_bars: pd.DataFrame,
    entry_ts: pd.Timestamp,
    entry_price: float,
    runtime_config: dict[str, Any],
) -> dict[str, float]:
    del prior_bars, entry_price
    or_minutes = _int_or_default(runtime_config.get("or_minutes", 30), 30)
    if or_minutes <= 0:
        return {}
    local_day = entry_ts.tz_convert("America/New_York").date()
    daily_widths: list[float] = []
    for _, day_bars in symbol_bars[symbol_bars["date"] <= local_day].groupby("date", sort=True):
        local_times = day_bars["timestamp"].dt.tz_convert("America/New_York")
        minutes_since_open = (local_times.dt.hour * 60 + local_times.dt.minute) - (9 * 60 + 30)
        opening = day_bars[
            (minutes_since_open >= 0)
            & (minutes_since_open < or_minutes)
            & ((day_bars["date"] < local_day) | (day_bars["timestamp"] <= entry_ts))
        ]
        if opening.empty:
            daily_widths.append(np.nan)
        else:
            daily_widths.append(float(opening["high"].max() - opening["low"].min()))
    current = daily_widths[-1] if daily_widths else np.nan
    prior = [value for value in daily_widths[-21:-1] if np.isfinite(value)]
    if not np.isfinite(current) or not prior:
        return {}
    return {"or_width_pctile": float(sum(value <= current for value in prior) / len(prior))}


def _int_or_default(value: object, default: int) -> int:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(parsed):
        return default
    return int(parsed)


_EXTRACTORS: dict[str, FamilyFeatureExtractor] = {
    "ema": _ema_entry_features,
    "orb": _orb_entry_features,
}
