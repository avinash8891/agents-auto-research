"""5 EMA signal generator u2014 vectorized.

Transcript rules:
  SHORT (5min): candle entirely above 5 EMA -> next bar breaks low -> entry
  LONG (15min): candle entirely below 5 EMA -> next bar breaks high -> entry
  Stop at alert candle opposite extreme. Daily reset (no overnight carry).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from numba_kernels import njit


@njit(cache=True)
def ema_alert_carry(
    high: np.ndarray,
    low: np.ndarray,
    ema: np.ndarray,
    same_day: np.ndarray,
    is_short: bool,
) -> tuple:
    """Stateful alert-carry for 5 EMA strategy.

    Alert candle stays armed until a subsequent bar breaks it or the day resets.
    Returns (entries, entry_prices, stop_prices, alert_bar_indices).
    """
    n = len(high)
    entries = np.zeros(n, dtype=np.bool_)
    entry_prices = np.full(n, np.nan)
    stop_prices = np.full(n, np.nan)
    alert_indices = np.full(n, -1, dtype=np.int64)
    alert_bar = -1

    for i in range(1, n):
        if not same_day[i]:
            alert_bar = -1

        prev_h = high[i - 1]
        prev_l = low[i - 1]
        prev_e = ema[i - 1]

        if is_short:
            if prev_l > prev_e:
                alert_bar = i - 1
            if alert_bar >= 0 and low[i] < low[alert_bar]:
                entries[i] = True
                entry_prices[i] = low[alert_bar]
                stop_prices[i] = high[alert_bar]
                alert_indices[i] = alert_bar
                alert_bar = -1
        else:
            if prev_h < prev_e:
                alert_bar = i - 1
            if alert_bar >= 0 and high[i] > high[alert_bar]:
                entries[i] = True
                entry_prices[i] = high[alert_bar]
                stop_prices[i] = low[alert_bar]
                alert_indices[i] = alert_bar
                alert_bar = -1

    return entries, entry_prices, stop_prices, alert_indices


@dataclass(frozen=True)
class EMASignals:
    entries: pd.Series
    direction: str
    entry_price: pd.Series
    stop_price: pd.Series
    alert_bar_idx: pd.Series


def generate_signals_for_frame(
    frame: pd.DataFrame,
    direction: str,
    ema_length: int = 5,
    use_range_shift: bool = False,
    range_shift_lookback: int = 20,
) -> EMASignals:
    """Vectorized 5 EMA signal generation."""
    close = frame["close"].values
    high = frame["high"].values
    low = frame["low"].values
    n = len(frame)

    if n < 2:
        return EMASignals(
            entries=pd.Series(np.zeros(n, dtype=bool), index=frame.index),
            direction=direction,
            entry_price=pd.Series(np.full(n, np.nan), index=frame.index),
            stop_price=pd.Series(np.full(n, np.nan), index=frame.index),
            alert_bar_idx=pd.Series(np.full(n, -1, dtype=int), index=frame.index),
        )

    # EMA
    ema = pd.Series(close).ewm(span=ema_length, adjust=False).mean().values
    if ema_length > 1:
        ema[: ema_length - 1] = np.nan

    # Shifted arrays (previous bar values)
    prev_high = np.empty(n)
    prev_high[0] = np.nan
    prev_high[1:] = high[:-1]
    prev_low = np.empty(n)
    prev_low[0] = np.nan
    prev_low[1:] = low[:-1]
    prev_ema = np.empty(n)
    prev_ema[0] = np.nan
    prev_ema[1:] = ema[:-1]

    # Daily reset: same day as previous bar
    if isinstance(frame.index, pd.DatetimeIndex):
        dates = frame.index.date
        same_day = np.zeros(n, dtype=bool)
        same_day[1:] = np.array([dates[i] == dates[i - 1] for i in range(1, n)])
    else:
        same_day = np.ones(n, dtype=bool)
        same_day[0] = False

    # Stateful alert-carry (numba JIT): transcript says "if next candle does
    # not break, consider the next candle" — alert stays armed until broken
    # or a new trading day resets it.
    entries, entry_prices, stop_prices, alert_indices = ema_alert_carry(
        high,
        low,
        ema,
        same_day,
        direction == "short",
    )

    # Range shift filter
    if use_range_shift:
        bias = _detect_range_shift(
            pd.Series(high, index=frame.index),
            pd.Series(low, index=frame.index),
            range_shift_lookback,
        )
        if direction == "long":
            entries = entries & (bias == 1)
        else:
            entries = entries & (bias == -1)
        entry_prices = np.where(entries, entry_prices, np.nan)
        stop_prices = np.where(entries, stop_prices, np.nan)

    return EMASignals(
        entries=pd.Series(entries, index=frame.index),
        direction=direction,
        entry_price=pd.Series(entry_prices, index=frame.index),
        stop_price=pd.Series(stop_prices, index=frame.index),
        alert_bar_idx=pd.Series(alert_indices, index=frame.index),
    )


def _detect_range_shift(high: pd.Series, low: pd.Series, lookback: int = 20):
    """Detect right range shift using rising/lower rolling range structure.
    Returns numpy array: 1=long bias, -1=short bias, 0=none.
    """
    completed_high = high.shift(1)
    completed_low = low.shift(1)
    range_high = completed_high.rolling(lookback, min_periods=lookback).max()
    range_low = completed_low.rolling(lookback, min_periods=lookback).min()

    prev_range_high = range_high.shift(lookback)
    prev_range_low = range_low.shift(lookback)

    right_shift = (range_high > prev_range_high) & (range_low > prev_range_low)
    left_shift = (range_high < prev_range_high) & (range_low < prev_range_low)

    bias = np.zeros(len(high), dtype=int)
    bias[right_shift.fillna(False).to_numpy()] = 1
    bias[left_shift.fillna(False).to_numpy()] = -1
    return bias
