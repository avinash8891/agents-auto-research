"""Numba-JIT kernels for intraday strategies.

All functions operate on numpy arrays only (no pandas).
Numba is a required runtime dependency for this module.
"""

from __future__ import annotations

import numpy as np
from numba import njit


@njit(cache=True)
def day_ids_from_ordinals(ordinals: np.ndarray) -> np.ndarray:
    """Convert date ordinals to 0-based day IDs."""
    n = len(ordinals)
    out = np.zeros(n, dtype=np.int64)
    day = 0
    for i in range(1, n):
        if ordinals[i] != ordinals[i - 1]:
            day += 1
        out[i] = day
    return out


@njit(cache=True)
def bar_of_day(day_ids: np.ndarray) -> np.ndarray:
    """0-based bar index within each day."""
    n = len(day_ids)
    out = np.zeros(n, dtype=np.int32)
    for i in range(1, n):
        if day_ids[i] == day_ids[i - 1]:
            out[i] = out[i - 1] + 1
        else:
            out[i] = 0
    return out


@njit(cache=True)
def first_signal_per_day(signals: np.ndarray, day_ids: np.ndarray) -> np.ndarray:
    """Keep only the first True per day per column. Modifies a copy."""
    n_bars, n_syms = signals.shape
    out = signals.copy()
    for c in range(n_syms):
        prev_day = -1
        found = False
        for i in range(n_bars):
            if day_ids[i] != prev_day:
                prev_day = day_ids[i]
                found = False
            if out[i, c]:
                if found:
                    out[i, c] = False
                else:
                    found = True
    return out


@njit(cache=True)
def compute_atr_nb(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int,
) -> np.ndarray:
    """ATR computation via numba."""
    n_bars, n_syms = high.shape
    atr = np.empty((n_bars, n_syms), dtype=np.float64)

    for c in range(n_syms):
        # True range
        tr = np.empty(n_bars, dtype=np.float64)
        prev_close = close[0, c]
        tr[0] = high[0, c] - low[0, c] if not np.isnan(high[0, c] + low[0, c]) else np.nan
        for i in range(1, n_bars):
            if np.isnan(high[i, c]) or np.isnan(low[i, c]):
                tr[i] = np.nan
                continue
            if np.isnan(prev_close):
                tr[i] = high[i, c] - low[i, c]
            else:
                hl = high[i, c] - low[i, c]
                hc = abs(high[i, c] - prev_close)
                lc = abs(low[i, c] - prev_close)
                tr[i] = max(hl, max(hc, lc))
            if not np.isnan(close[i, c]):
                prev_close = close[i, c]

        # Rolling mean
        cumsum = 0.0
        count = 0
        for i in range(n_bars):
            if not np.isnan(tr[i]):
                cumsum += tr[i]
                count += 1
            if i >= period and not np.isnan(tr[i - period]):
                cumsum -= tr[i - period]
                count -= 1
            atr[i, c] = cumsum / count if count > 0 else np.nan

    return atr
