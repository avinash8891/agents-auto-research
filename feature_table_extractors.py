from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

FamilyFeatureExtractor = Callable[
    [pd.DataFrame, pd.DataFrame, pd.Timestamp, float, dict[str, Any], dict[tuple[Any, ...], Any]],
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
    feature_cache: dict[tuple[Any, ...], Any] | None = None,
) -> dict[str, float]:
    features: dict[str, float] = {column: np.nan for column in FAMILY_ENTRY_FEATURE_COLUMNS}
    extractor = _EXTRACTORS.get(str(family).lower())
    if extractor is None:
        return features
    if feature_cache is None:
        feature_cache = {}
    features.update(
        extractor(symbol_bars, prior_bars, entry_ts, entry_price, runtime_config, feature_cache)
    )
    return features


def _ema_entry_features(
    symbol_bars: pd.DataFrame,
    prior_bars: pd.DataFrame,
    entry_ts: pd.Timestamp,
    entry_price: float,
    runtime_config: dict[str, Any],
    feature_cache: dict[tuple[Any, ...], Any],
) -> dict[str, float]:
    del entry_ts
    if prior_bars.empty or not np.isfinite(entry_price) or entry_price == 0:
        return {}
    ema_length = _int_or_default(runtime_config.get("ema_length", 5), 5)
    if ema_length <= 0:
        return {}
    # adjust=False EMA is a left-to-right recursion: the value at position i of
    # the full series equals the EMA of the prefix ending at i, so the series
    # is computed once per (symbol, length) and indexed per trade.
    # prior_bars is always a contiguous prefix of symbol_bars (see _feature_row).
    symbol = (
        str(symbol_bars["symbol"].iloc[0])
        if not symbol_bars.empty and "symbol" in symbol_bars
        else ""
    )
    cache_key = ("ema_series", symbol, ema_length)
    ema_series = feature_cache.get(cache_key)
    if ema_series is None:
        ema_series = (
            symbol_bars["close"].astype(float).ewm(span=ema_length, adjust=False).mean().to_numpy()
        )
        feature_cache[cache_key] = ema_series
    ema = float(ema_series[len(prior_bars) - 1])
    return {"dist_to_ema_pct": float((entry_price - ema) / entry_price * 100.0)}


def _orb_entry_features(
    symbol_bars: pd.DataFrame,
    prior_bars: pd.DataFrame,
    entry_ts: pd.Timestamp,
    entry_price: float,
    runtime_config: dict[str, Any],
    feature_cache: dict[tuple[Any, ...], Any],
) -> dict[str, float]:
    del prior_bars, entry_price
    or_minutes = _int_or_default(runtime_config.get("or_minutes", 30), 30)
    if or_minutes <= 0:
        return {}
    local_day = entry_ts.tz_convert("America/New_York").date()
    daily_widths_by_date = _full_orb_opening_widths(
        symbol_bars,
        or_minutes,
        feature_cache=feature_cache,
    )
    day_bars_by_date = _day_bars_by_date(symbol_bars, feature_cache=feature_cache)
    daily_widths: list[float] = []
    for day, width in daily_widths_by_date.items():
        if day > local_day:
            continue
        if day == local_day:
            width = _entry_day_orb_width(
                day_bars_by_date.get(day, pd.DataFrame()), entry_ts, or_minutes
            )
        daily_widths.append(width)
    current = daily_widths[-1] if daily_widths else np.nan
    prior = [value for value in daily_widths[-21:-1] if np.isfinite(value)]
    if not np.isfinite(current) or not prior:
        return {}
    return {"or_width_pctile": float(sum(value <= current for value in prior) / len(prior))}


def _full_orb_opening_widths(
    symbol_bars: pd.DataFrame,
    or_minutes: int,
    *,
    feature_cache: dict[tuple[Any, ...], Any],
) -> dict[object, float]:
    symbol = (
        str(symbol_bars["symbol"].iloc[0])
        if not symbol_bars.empty and "symbol" in symbol_bars
        else ""
    )
    cache_key = ("orb_opening_widths", symbol, or_minutes)
    cached = feature_cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    widths: dict[object, float] = {}
    for day, day_bars in symbol_bars.groupby("date", sort=True):
        widths[day] = _entry_day_orb_width(day_bars, None, or_minutes)
    feature_cache[cache_key] = widths
    return widths


def _day_bars_by_date(
    symbol_bars: pd.DataFrame,
    *,
    feature_cache: dict[tuple[Any, ...], Any],
) -> dict[object, pd.DataFrame]:
    symbol = (
        str(symbol_bars["symbol"].iloc[0])
        if not symbol_bars.empty and "symbol" in symbol_bars
        else ""
    )
    cache_key = ("bars_by_date", symbol)
    cached = feature_cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    grouped = {day: day_bars for day, day_bars in symbol_bars.groupby("date", sort=False)}
    feature_cache[cache_key] = grouped
    return grouped


def _entry_day_orb_width(
    day_bars: pd.DataFrame,
    entry_ts: pd.Timestamp | None,
    or_minutes: int,
) -> float:
    if day_bars.empty:
        return np.nan
    local_times = day_bars["timestamp"].dt.tz_convert("America/New_York")
    minutes_since_open = (local_times.dt.hour * 60 + local_times.dt.minute) - (9 * 60 + 30)
    opening_mask = (minutes_since_open >= 0) & (minutes_since_open < or_minutes)
    if entry_ts is not None:
        opening_mask &= day_bars["timestamp"] < entry_ts
    opening = day_bars[opening_mask]
    if opening.empty:
        return np.nan
    return float(opening["high"].max() - opening["low"].min())


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
