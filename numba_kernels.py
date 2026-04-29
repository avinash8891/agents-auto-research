"""Numba-JIT kernels for intraday strategies.

Falls back to pure numpy if numba is not available.
All functions operate on numpy arrays only (no pandas).
"""
from __future__ import annotations

import numpy as np

try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
    # Fallback: identity decorator — functions run as plain Python
    def njit(*args, **kwargs):
        def wrapper(fn):
            return fn
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return wrapper


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
        tr[0] = high[0, c] - low[0, c]
        for i in range(1, n_bars):
            hl = high[i, c] - low[i, c]
            hc = abs(high[i, c] - close[i - 1, c])
            lc = abs(low[i, c] - close[i - 1, c])
            tr[i] = max(hl, max(hc, lc))

        # Rolling mean
        cumsum = 0.0
        for i in range(n_bars):
            cumsum += tr[i]
            if i < period:
                atr[i, c] = cumsum / (i + 1)
            else:
                cumsum -= tr[i - period]
                atr[i, c] = cumsum / period

    return atr
