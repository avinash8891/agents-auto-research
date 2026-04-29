"""Day-level regime classification and trade gating for ORB.

Vectorized u2014 classifies all days x symbols using numpy/pandas groupby,
no Python per-day per-symbol loops.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from experiment_schema import canonical_regimes
from numba_kernels import njit


@njit(cache=True)
def chop_cross_count(
    close: np.ndarray,
    midpoint_intra: np.ndarray,
    day_idx: np.ndarray,
    n_days: int,
    n_syms: int,
) -> np.ndarray:
    """Count midpoint crosses per day per symbol."""
    counts = np.zeros((n_days, n_syms), dtype=np.int32)
    n_bars = len(day_idx)
    for c in range(n_syms):
        prev_above = False
        prev_day = -1
        for i in range(n_bars):
            d = day_idx[i]
            mid = midpoint_intra[i, c]
            if np.isnan(mid):
                continue
            above = close[i, c] > mid
            if d == prev_day and above != prev_above:
                counts[d, c] += 1
            prev_above = above
            prev_day = d
    return counts


REGIME_NAMES = canonical_regimes()


def classify_regimes(
    open_: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    or_high: pd.DataFrame | None = None,
    or_low: pd.DataFrame | None = None,
    atr_period: int = 14,
    atr_lookback: int = 60,
    trend_range_pct: float = 0.70,
    chop_crosses_min: int = 3,
    wide_or_mult: float = 1.5,
    narrow_or_mult: float = 0.5,
) -> dict[str, pd.DataFrame]:
    """Classify each trading day into regime labels. Vectorized.

    Returns a dict of {regime_name: DataFrame(index=dates, columns=symbols, values=bool)}.
    Each day can have multiple regimes (e.g., "trend" AND "high-vol").
    """
    dates_raw = close.index.date
    symbols = close.columns

    # Daily OHLC via groupby
    daily_open = open_.groupby(dates_raw).first()
    daily_high = high.groupby(dates_raw).max()
    daily_low = low.groupby(dates_raw).min()
    daily_close = close.groupby(dates_raw).last()
    daily_range = daily_high - daily_low

    dates = daily_close.index  # unique dates
    n_days = len(dates)
    n_syms = len(symbols)

    # --- Trend: close beyond 70% of day's range from open ---
    close_dist = (daily_close - daily_open).abs()
    trend_mask = (close_dist >= trend_range_pct * daily_range) & (daily_range > 0)

    # --- ATR and vol regimes ---
    # True range on daily bars
    prev_close = daily_close.shift(1)
    tr = pd.DataFrame(
        np.maximum(
            (daily_high - daily_low).values,
            np.maximum(
                np.abs(daily_high.values - prev_close.values),
                np.abs(daily_low.values - prev_close.values),
            ),
        ),
        index=dates, columns=symbols,
    )
    # Handle first row
    tr.iloc[0] = (daily_high.iloc[0] - daily_low.iloc[0]).values

    daily_atr = tr.rolling(atr_period, min_periods=1).mean()
    atr_q75 = daily_atr.rolling(atr_lookback, min_periods=10).quantile(0.75)
    atr_q25 = daily_atr.rolling(atr_lookback, min_periods=10).quantile(0.25)

    high_vol_mask = daily_atr >= atr_q75
    low_vol_mask = daily_atr <= atr_q25

    # --- Chop: price crosses OR midpoint 3+ times ---
    chop_mask = pd.DataFrame(False, index=dates, columns=symbols)
    if or_high is not None and or_low is not None:
        or_h_daily = or_high.groupby(dates_raw).first()
        or_l_daily = or_low.groupby(dates_raw).first()
        midpoint_daily = (or_h_daily + or_l_daily) / 2

        # Broadcast daily midpoint to intraday using day index
        day_ids = np.array([d.toordinal() for d in dates_raw], dtype=np.int64)
        unique_ords = np.array([d.toordinal() for d in dates], dtype=np.int64)
        day_idx = np.searchsorted(unique_ords, day_ids).astype(np.int64)
        mid_intraday = midpoint_daily.values[day_idx]

        # Numba kernel: count crosses per day per symbol
        cross_counts = chop_cross_count(
            close.values, mid_intraday, day_idx, n_days, n_syms,
        )
        chop_mask = pd.DataFrame(
            cross_counts >= chop_crosses_min, index=dates, columns=symbols,
        )

    # --- Wide/narrow OR ---
    wide_or_mask = pd.DataFrame(False, index=dates, columns=symbols)
    narrow_or_mask = pd.DataFrame(False, index=dates, columns=symbols)
    if or_high is not None and or_low is not None:
        or_width_daily = or_h_daily - or_l_daily
        median_or = or_width_daily.rolling(20, min_periods=5).median()
        wide_or_mask = (or_width_daily > wide_or_mult * median_or) & (median_or > 0)
        narrow_or_mask = (or_width_daily < narrow_or_mult * median_or) & (median_or > 0)

    # LOOK-AHEAD FIX: only lag regimes that use full-day data.
    #
    # Per research (TradingStats 6142-day study, Crabel 1990, Holmberg 2013):
    # - chop: counts midpoint crosses through the ENTIRE day -> look-ahead
    # - trend: uses close vs open relative to day range -> look-ahead
    # - high-vol/low-vol: 14-day ATR from prior closes -> NO look-ahead
    # - wide-OR/narrow-OR: OR width known at 10:00 AM -> NO look-ahead
    #
    # Lagging chop/trend by 1 day is consistent with volatility clustering
    # (Crabel NR4/NR7, Ding 2012) but is an untested hypothesis for ORB.
    needs_lag = {"chop", "trend"}

    result = {}
    for name, df in [("trend", trend_mask), ("chop", chop_mask),
                     ("high-vol", high_vol_mask), ("low-vol", low_vol_mask),
                     ("wide-OR", wide_or_mask), ("narrow-OR", narrow_or_mask)]:
        if name in needs_lag:
            shifted = df.shift(1)
            shifted.iloc[0] = False
            result[name] = shifted.astype(bool)
        else:
            result[name] = df

    return result


def get_primary_regime(regime_dict: dict[str, pd.DataFrame], date, sym) -> str:
    """Return a single primary regime label for a given date/symbol."""
    priority = ["trend", "chop", "high-vol", "low-vol", "wide-OR", "narrow-OR"]
    for r in priority:
        df = regime_dict.get(r)
        if df is not None and date in df.index and sym in df.columns:
            if df.at[date, sym]:
                return r
    return "neutral"


def apply_regime_gate(
    trades_df: pd.DataFrame,
    regime_dict: dict[str, pd.DataFrame],
    skip_regimes: set[str] | None = None,
    require_regimes: set[str] | None = None,
) -> pd.DataFrame:
    """Filter trades by regime.

    skip_regimes: discard trades on days matching ANY of these regimes.
    require_regimes: keep only trades on days matching AT LEAST ONE of these regimes.
    """
    if trades_df.empty:
        return trades_df
    if not skip_regimes and not require_regimes:
        return trades_df

    entry_dates = trades_df["entry_date"].dt.date if hasattr(trades_df["entry_date"].iloc[0], "date") else trades_df["entry_date"]
    syms = trades_df["symbol"]

    keep_mask = np.ones(len(trades_df), dtype=bool)

    for i in range(len(trades_df)):
        d = entry_dates.iloc[i] if hasattr(entry_dates.iloc[i], 'date') else entry_dates.iloc[i]
        if hasattr(d, 'date'):
            d = d.date()
        s = syms.iloc[i]

        # Skip check
        if skip_regimes:
            for regime_name in skip_regimes:
                df = regime_dict.get(regime_name)
                if df is not None and d in df.index and s in df.columns and df.at[d, s]:
                    keep_mask[i] = False
                    break

        # Require check (must match at least one)
        if require_regimes and keep_mask[i]:
            matched = False
            for regime_name in require_regimes:
                df = regime_dict.get(regime_name)
                if df is not None and d in df.index and s in df.columns and df.at[d, s]:
                    matched = True
                    break
            if not matched:
                keep_mask[i] = False

    return trades_df[keep_mask].reset_index(drop=True)


def compute_regime_expectancy(
    trades_df: pd.DataFrame,
    regime_dict: dict[str, pd.DataFrame],
) -> dict[str, float]:
    """Compute mean PnL (expectancy) per primary regime."""
    if trades_df.empty:
        return {}

    entry_dates = trades_df["entry_date"]
    syms = trades_df["symbol"]
    pnls = trades_df["pnl_pct"].values

    # Assign primary regime to each trade
    regimes = []
    for i in range(len(trades_df)):
        d = entry_dates.iloc[i]
        if hasattr(d, "date"):
            d = d.date()
        s = syms.iloc[i]
        regimes.append(get_primary_regime(regime_dict, d, s))

    regime_arr = np.array(regimes)
    result = {}
    for r in np.unique(regime_arr):
        mask = regime_arr == r
        result[r] = round(float(pnls[mask].mean()), 4)
    return result
