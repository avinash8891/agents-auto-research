"""Opening Range Breakout strategy core logic.

Computes the opening range (OR) from the first N minutes of trading,
then generates long/short entries when price breaks above/below the range.
Fully vectorized u2014 no Python per-day or per-symbol loops.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from numba_kernels import bar_of_day as _bar_of_day_nb
from numba_kernels import (
    day_ids_from_ordinals,
)
from numba_kernels import first_signal_per_day as _first_signal_nb


@dataclass(frozen=True)
class ORBSignals:
    """Output of the ORB signal generator."""

    entries_long: pd.DataFrame  # True on bar of long entry
    entries_short: pd.DataFrame  # True on bar of short entry
    stop_price: pd.DataFrame  # SL price per entry bar
    target_price: pd.DataFrame  # TP price per entry bar
    or_high: pd.DataFrame  # Opening range high per day
    or_low: pd.DataFrame  # Opening range low per day
    or_width: pd.DataFrame  # OR high - OR low per day
    rvol: pd.DataFrame | None = None  # Per-day RVOL ratio (None if gate disabled)


def _day_ids(index: pd.DatetimeIndex) -> np.ndarray:
    """Return integer day-id per bar. Uses numba kernel."""
    ordinals = np.array([d.toordinal() for d in index.date], dtype=np.int64)
    return day_ids_from_ordinals(ordinals)


def _bar_of_day(day_ids: np.ndarray) -> np.ndarray:
    """Return 0-based bar index within each day. Uses numba kernel."""
    return _bar_of_day_nb(day_ids)


def compute_opening_range(
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    or_minutes: int = 30,
    timeframe_minutes: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Compute opening range (high, low, width) for each day. Vectorized."""
    bars_in_or = max(1, or_minutes // timeframe_minutes)
    n_bars, n_syms = high.shape

    day_ids = _day_ids(high.index)
    bod = _bar_of_day(day_ids)

    high_arr = high.values
    low_arr = low.values

    # Mask: bars within the opening range (bar_of_day < bars_in_or)
    or_mask = bod < bars_in_or  # (n_bars,)

    # For each day, compute max(high) and min(low) over OR bars.
    # Strategy: set non-OR bars to -inf/+inf, then groupby day.
    high_masked = np.where(or_mask[:, None], high_arr, -np.inf)
    low_masked = np.where(or_mask[:, None], low_arr, np.inf)

    # Find unique days and their boundaries
    unique_days, day_start_idx = np.unique(day_ids, return_index=True)
    n_days = len(unique_days)

    # Day end indices
    day_end_idx = np.empty(n_days, dtype=np.int64)
    day_end_idx[:-1] = day_start_idx[1:]
    day_end_idx[-1] = n_bars

    # Compute OR high/low per day
    or_high_daily = np.full((n_days, n_syms), np.nan)
    or_low_daily = np.full((n_days, n_syms), np.nan)

    for i in range(n_days):
        s, e = day_start_idx[i], day_end_idx[i]
        day_high = high_masked[s:e]
        day_low = low_masked[s:e]
        h = day_high.max(axis=0)
        low_values = day_low.min(axis=0)
        # If no OR bars (day too short), leave as nan
        h[h == -np.inf] = np.nan
        low_values[low_values == np.inf] = np.nan
        or_high_daily[i] = h
        or_low_daily[i] = low_values

    # Broadcast daily values back to intraday index
    # day_ids maps each bar to its day index
    or_high_arr = or_high_daily[day_ids]
    or_low_arr = or_low_daily[day_ids]

    or_high_df = pd.DataFrame(or_high_arr, index=high.index, columns=high.columns)
    or_low_df = pd.DataFrame(or_low_arr, index=low.index, columns=low.columns)
    or_width_df = or_high_df - or_low_df

    return or_high_df, or_low_df, or_width_df


def compute_median_or_width(
    or_width: pd.DataFrame,
    lookback_days: int = 20,
) -> pd.DataFrame:
    """Rolling median OR width over lookback_days trading days. Vectorized."""
    day_ids = _day_ids(or_width.index)

    # Extract one value per day (first bar)
    unique_days, first_bar_idx = np.unique(day_ids, return_index=True)
    daily_width = or_width.values[first_bar_idx]  # (n_days, n_syms)

    # Rolling median on daily array
    n_days, n_syms = daily_width.shape
    median_daily = np.full_like(daily_width, np.nan)
    for i in range(lookback_days - 1, n_days):
        window = daily_width[max(0, i - lookback_days + 1) : i + 1]
        median_daily[i] = np.nanmedian(window, axis=0)
    # Fill initial bars with expanding median
    for i in range(min(lookback_days - 1, n_days)):
        if i >= 4:  # min_periods=5
            window = daily_width[: i + 1]
            median_daily[i] = np.nanmedian(window, axis=0)

    # Broadcast back to intraday
    result_arr = median_daily[day_ids]
    return pd.DataFrame(result_arr, index=or_width.index, columns=or_width.columns)


def _first_signal_per_day(signals: pd.DataFrame, day_ids: np.ndarray) -> pd.DataFrame:
    """Keep only the first True per day per column. Uses numba kernel."""
    arr = _first_signal_nb(signals.values.astype(np.bool_), day_ids)
    return pd.DataFrame(arr, index=signals.index, columns=signals.columns)


def compute_rvol_gate(
    volume: pd.DataFrame,
    day_ids: np.ndarray,
    lookback_days: int = 14,
    threshold: float = 1.5,
    baseline_stat: str = "mean",
    min_history_days: int = 14,
    computation: str = "first_bar",
    bars_in_or: int = 1,
) -> tuple[np.ndarray, pd.DataFrame]:
    """Compute RVOL gate: volume metric vs historical baseline.

    computation='first_bar': first-bar volume vs historical same-bar baseline.
    computation='cumulative_or_window': sum of volume over all OR bars vs
        trailing mean of the same cumulative OR-window volume over prior days.

    Returns (gate_pass, rvol_df) where gate_pass is bool array (n_bars, n_syms)
    broadcast to all intraday bars, and rvol_df has the per-day ratio.
    """
    vol_arr = volume.values
    n_bars, n_syms = vol_arr.shape

    unique_days, first_bar_idx = np.unique(day_ids, return_index=True)
    n_days = len(unique_days)

    day_end_idx = np.empty(n_days, dtype=np.int64)
    day_end_idx[:-1] = first_bar_idx[1:]
    day_end_idx[-1] = n_bars

    if computation == "cumulative_or_window":
        day_vol = np.full((n_days, n_syms), np.nan)
        for i in range(n_days):
            s = first_bar_idx[i]
            e = min(s + bars_in_or, day_end_idx[i])
            day_vol[i] = np.nansum(vol_arr[s:e], axis=0)
    else:
        day_vol = vol_arr[first_bar_idx]  # (n_days, n_syms)

    agg_fn = np.nanmean if baseline_stat == "mean" else np.nanmedian

    rvol_daily = np.full((n_days, n_syms), np.nan)
    gate_daily = np.zeros((n_days, n_syms), dtype=np.bool_)

    for i in range(n_days):
        start = max(0, i - lookback_days)
        history = day_vol[start:i]  # exclude current day
        if len(history) == 0:
            continue
        valid_count = np.sum(~np.isnan(history), axis=0)
        baseline = agg_fn(history, axis=0)

        ratio = np.where(baseline > 0, day_vol[i] / baseline, np.nan)
        rvol_daily[i] = ratio
        gate_daily[i] = (ratio >= threshold) & (valid_count >= min_history_days)

    gate_broadcast = gate_daily[day_ids]
    rvol_broadcast = rvol_daily[day_ids]
    rvol_df = pd.DataFrame(rvol_broadcast, index=volume.index, columns=volume.columns)

    return gate_broadcast, rvol_df


def generate_orb_signals(
    open_: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    volume: pd.DataFrame | None = None,
    config: dict | None = None,
) -> ORBSignals:
    """Generate ORB breakout signals. Vectorized."""
    cfg = config or {}
    or_minutes = cfg.get("or_minutes", 30)
    timeframe_minutes = cfg.get("timeframe_minutes", 5)
    rr_ratio = cfg.get("rr_ratio", 2.0)
    max_one_entry_per_day = cfg.get("max_one_entry_per_day", True)

    use_volume_filter = cfg.get("use_volume_filter", False)
    use_trend_filter = cfg.get("use_trend_filter", False)
    use_wide_or_filter = cfg.get("use_wide_or_filter", False)
    use_follow_through = cfg.get("use_follow_through", False)
    long_only = cfg.get("long_only", False)
    wide_or_mult = cfg.get("wide_or_mult", 1.5)
    trend_ema_period = cfg.get("trend_ema_period", 20)
    volume_lookback = cfg.get("volume_lookback", 20)

    use_rvol_gate = cfg.get("use_rvol_gate", False)
    rvol_lookback_days = cfg.get("rvol_lookback_days", 14)
    rvol_threshold = cfg.get("rvol_threshold", 1.5)
    rvol_baseline_stat = cfg.get("rvol_baseline_stat", "mean")
    rvol_min_history_days = cfg.get("rvol_min_history_days", 14)
    rvol_computation = cfg.get("rvol_computation", "first_bar")

    outside_or_bar_policy = cfg.get("outside_or_bar_policy", "none")

    bars_in_or = max(1, or_minutes // timeframe_minutes)
    day_ids = _day_ids(close.index)
    bod = _bar_of_day(day_ids)

    # Compute opening range
    or_high, or_low, or_width = compute_opening_range(
        high,
        low,
        close,
        or_minutes=or_minutes,
        timeframe_minutes=timeframe_minutes,
    )
    median_or = compute_median_or_width(or_width)

    # After-OR mask
    after_or = bod >= bars_in_or  # numpy bool array

    # Raw breakout conditions
    close_arr = close.values
    or_high_arr = or_high.values
    or_low_arr = or_low.values

    broke_above = (close_arr > or_high_arr) & after_or[:, None]
    broke_below = (close_arr < or_low_arr) & after_or[:, None]

    # Follow-through filter
    if use_follow_through:
        # Require 2 consecutive closes above/below OR level (confirmation bar).
        # Shift breakout condition forward by 1 bar: entry on the bar AFTER
        # the breakout bar confirms.
        prev_broke_above = np.roll(close_arr > or_high_arr, 1, axis=0) & after_or[:, None]
        prev_broke_above[0] = False
        broke_above = broke_above & prev_broke_above

        prev_broke_below = np.roll(close_arr < or_low_arr, 1, axis=0) & after_or[:, None]
        prev_broke_below[0] = False
        broke_below = broke_below & prev_broke_below

    # Volume filter
    if use_volume_filter and volume is not None:
        median_vol = volume.rolling(volume_lookback, min_periods=5).median().values
        vol_ok = volume.values >= median_vol
        broke_above = broke_above & vol_ok
        broke_below = broke_below & vol_ok

    # RVOL gate
    rvol_df = None
    if use_rvol_gate and volume is not None:
        rvol_pass, rvol_df = compute_rvol_gate(
            volume,
            day_ids,
            lookback_days=rvol_lookback_days,
            threshold=rvol_threshold,
            baseline_stat=rvol_baseline_stat,
            min_history_days=rvol_min_history_days,
            computation=rvol_computation,
            bars_in_or=bars_in_or,
        )
        broke_above = broke_above & rvol_pass
        broke_below = broke_below & rvol_pass

    # Outside-OR-bar skip: if a bar trades through BOTH OR_high and OR_low,
    # disqualify the symbol for the remainder of the day.
    if outside_or_bar_policy == "skip":
        high_arr_vals = high.values
        low_arr_vals = low.values
        outside_bar = (
            (high_arr_vals > or_high_arr) & (low_arr_vals < or_low_arr) & after_or[:, None]
        )
        # Forward-fill the skip flag within each day
        unique_days_arr, day_start_idx = np.unique(day_ids, return_index=True)
        n_days_arr = len(unique_days_arr)
        day_end_arr = np.empty(n_days_arr, dtype=np.int64)
        day_end_arr[:-1] = day_start_idx[1:]
        day_end_arr[-1] = len(day_ids)
        skip_mask = np.zeros_like(outside_bar)
        for d_i in range(n_days_arr):
            s, e = day_start_idx[d_i], day_end_arr[d_i]
            day_outside = outside_bar[s:e]
            day_skip = np.zeros_like(day_outside)
            for col in range(day_outside.shape[1]):
                first_outside = np.argmax(day_outside[:, col])
                if day_outside[first_outside, col]:
                    day_skip[first_outside:, col] = True
            skip_mask[s:e] = day_skip
        broke_above = broke_above & ~skip_mask
        broke_below = broke_below & ~skip_mask

    # Trend alignment filter
    if use_trend_filter:
        daily_close = close.groupby(close.index.date).last()
        daily_ema = daily_close.ewm(span=trend_ema_period, adjust=False).mean()
        # Map previous day's EMA to intraday bars
        ema_daily_arr = daily_ema.values  # (n_days, n_syms)
        unique_days, first_bar_idx = np.unique(day_ids, return_index=True)
        # Shift by 1 day: today uses yesterday's EMA
        prev_ema = np.full_like(ema_daily_arr, np.nan)
        prev_ema[1:] = ema_daily_arr[:-1]
        # Broadcast to intraday
        ema_intraday = prev_ema[day_ids]
        trend_up = close_arr > ema_intraday
        trend_down = close_arr < ema_intraday
        broke_above = broke_above & trend_up
        broke_below = broke_below & trend_down

    # Wide OR filter
    if use_wide_or_filter:
        wide_mask = or_width.values > (wide_or_mult * median_or.values)
        broke_above = broke_above & ~wide_mask
        broke_below = broke_below & ~wide_mask

    # Long-only mode: suppress short entries
    if long_only:
        broke_below = np.zeros_like(broke_below)

    # Convert to DataFrames for _first_signal_per_day
    broke_above_df = pd.DataFrame(broke_above, index=close.index, columns=close.columns)
    broke_below_df = pd.DataFrame(broke_below, index=close.index, columns=close.columns)

    if max_one_entry_per_day:
        broke_above_df = _first_signal_per_day(broke_above_df, day_ids)
        broke_below_df = _first_signal_per_day(broke_below_df, day_ids)

    # Shift signals forward by 1 bar: breakout detected on bar N,
    # entry happens on bar N+1's open (no look-ahead bias).
    # Also suppress if the next bar is a new day (don't enter overnight).
    broke_above_arr = np.roll(broke_above_df.values, 1, axis=0)
    broke_above_arr[0] = False
    broke_below_arr = np.roll(broke_below_df.values, 1, axis=0)
    broke_below_arr[0] = False
    # Suppress cross-day entries: signal bar must be same day as entry bar
    prev_day_ids = np.roll(day_ids, 1)
    prev_day_ids[0] = -1
    same_day = day_ids == prev_day_ids
    broke_above_arr = broke_above_arr & same_day[:, None]
    broke_below_arr = broke_below_arr & same_day[:, None]
    broke_above_df = pd.DataFrame(broke_above_arr, index=close.index, columns=close.columns)
    broke_below_df = pd.DataFrame(broke_below_arr, index=close.index, columns=close.columns)

    # Entry price = open of the entry bar (next bar after signal)
    open_arr = open_.values

    # Stop and target prices
    stop_arr = np.full(close_arr.shape, np.nan)
    target_arr = np.full(close_arr.shape, np.nan)

    long_mask = broke_above_df.values
    short_mask = broke_below_df.values

    # Long: stop at OR low, target at entry + rr * risk
    stop_arr[long_mask] = or_low.values[long_mask]
    stop_arr[short_mask] = or_high.values[short_mask]
    target_arr[long_mask] = (open_arr + rr_ratio * (open_arr - or_low.values))[long_mask]
    target_arr[short_mask] = (open_arr - rr_ratio * (or_high.values - open_arr))[short_mask]

    stop_price = pd.DataFrame(stop_arr, index=close.index, columns=close.columns)
    target_price = pd.DataFrame(target_arr, index=close.index, columns=close.columns)

    return ORBSignals(
        entries_long=broke_above_df,
        entries_short=broke_below_df,
        stop_price=stop_price,
        target_price=target_price,
        or_high=or_high,
        or_low=or_low,
        or_width=or_width,
        rvol=rvol_df,
    )
