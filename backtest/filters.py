from __future__ import annotations

import numpy as np
import pandas as pd


def _compute_gap_up_days(open_: pd.Series, close: pd.Series, gap_pct: float) -> set:
    """Return set of dates where the stock gapped up >= gap_pct from previous close."""
    daily_open = open_.groupby(open_.index.date).first()
    daily_close = close.groupby(close.index.date).last()
    prev_close = daily_close.shift(1)
    gap = (daily_open - prev_close) / prev_close
    return set(gap[gap >= gap_pct].index)


def _compute_gap_down_days(open_: pd.Series, close: pd.Series, gap_pct: float) -> set:
    """Return set of dates where the stock gapped down >= gap_pct from previous close."""
    daily_open = open_.groupby(open_.index.date).first()
    daily_close = close.groupby(close.index.date).last()
    prev_close = daily_close.shift(1)
    gap = (prev_close - daily_open) / prev_close
    return set(gap[gap >= gap_pct].index)


def _filter_signals_to_days(signals, frame: pd.DataFrame, allowed_days: set):
    """Zero out signals on days not in allowed_days."""
    if not allowed_days:
        signals.entries[:] = False
        signals.entry_price[:] = np.nan
        signals.stop_price[:] = np.nan
        if hasattr(signals, "alert_bar_idx"):
            signals.alert_bar_idx[:] = -1
        return signals
    dates = frame.index.date
    mask = np.isin(dates, list(allowed_days))
    signals.entries[~mask] = False
    signals.entry_price[~mask] = np.nan
    signals.stop_price[~mask] = np.nan
    if hasattr(signals, "alert_bar_idx"):
        signals.alert_bar_idx[~mask] = -1
    return signals


def _exclude_signals_on_days(signals, frame: pd.DataFrame, excluded_days: set):
    """Zero out signals on days IN excluded_days (opposite of _filter_signals_to_days)."""
    if not excluded_days:
        return signals
    dates = frame.index.date
    mask = np.isin(dates, list(excluded_days))
    signals.entries[mask] = False
    signals.entry_price[mask] = np.nan
    signals.stop_price[mask] = np.nan
    return signals


def _exclude_signals_before_bar(signals, frame: pd.DataFrame, min_bars: int):
    """Zero out signals on the first `min_bars` bars of each trading day.

    bars_since_open < min_bars are excluded (e.g. min_bars=1 drops the opening
    bar). Position-within-day matches feature_table's bars_since_open (group by
    calendar day, 0-indexed), so a conductor rule like bars_since_open == 0 is
    tested faithfully. min_bars <= 0 is a no-op.
    """
    if not min_bars or int(min_bars) <= 0:
        return signals
    if not isinstance(frame.index, pd.DatetimeIndex) or len(frame.index) == 0:
        return signals
    bars_since_open = (
        pd.Series(0, index=frame.index).groupby(frame.index.normalize()).cumcount().to_numpy()
    )
    mask = bars_since_open < int(min_bars)
    signals.entries[mask] = False
    signals.entry_price[mask] = np.nan
    signals.stop_price[mask] = np.nan
    return signals
