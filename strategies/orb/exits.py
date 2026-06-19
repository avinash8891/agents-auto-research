"""Exit rule variants for ORB strategy.

Uses numba-JIT trade simulation kernel when available.
Falls back to numpy-only path otherwise.
"""

from __future__ import annotations

from datetime import time as dt_time

import numpy as np
import pandas as pd

from numba_kernels import compute_atr_nb, njit
from strategies.orb.defaults import get_orb_defaults


@njit(cache=True)
def simulate_trades(
    close: np.ndarray,  # (n_bars, n_syms)
    high: np.ndarray,
    low: np.ndarray,
    open_: np.ndarray,  # (n_bars, n_syms) u2014 for SL/TP ambiguity resolution
    or_high: np.ndarray,
    or_low: np.ndarray,
    stop_arr: np.ndarray,
    target_arr: np.ndarray,
    entry_rows: np.ndarray,  # (n_trades,) int64
    entry_cols: np.ndarray,  # (n_trades,) int64
    is_long: np.ndarray,  # (n_trades,) bool
    dates_int: np.ndarray,  # (n_bars,) int32 u2014 ordinal per bar
    time_cutoff_mask: np.ndarray,  # (n_bars,) bool u2014 past time cutoff
    atr: np.ndarray,  # (n_bars, n_syms) or empty (0,0)
    slippage: float,
    max_hold: int,
    use_time_stop: bool,
    use_failed_breakout: bool,
    use_vol_trail: bool,
    use_opposite_break: bool,
    vol_trail_mult: float,
    conservative_sl_fill: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Simulate all trades and return exit info.

    Returns:
        exit_rows: (n_trades,) int64
        exit_prices: (n_trades,) float64
        entry_prices: (n_trades,) float64
        pnl: (n_trades,) float64
        exit_reason_codes: (n_trades,) int32
            0=invalid, 1=stop_loss, 2=target, 3=time_stop,
            4=failed_breakout, 5=vol_trail, 6=opposite_break,
            7=eod, 8=timeout
    """
    n_bars = close.shape[0]
    n_trades = len(entry_rows)
    has_atr = atr.shape[0] > 0 and atr.shape[1] > 0

    exit_rows = np.empty(n_trades, dtype=np.int64)
    exit_prices = np.empty(n_trades, dtype=np.float64)
    entry_prices = np.empty(n_trades, dtype=np.float64)
    pnl = np.empty(n_trades, dtype=np.float64)
    exit_reason_codes = np.empty(n_trades, dtype=np.int32)

    for t in range(n_trades):
        e_row = entry_rows[t]
        e_col = entry_cols[t]
        long = is_long[t]

        e_price = open_[e_row, e_col]
        sl = stop_arr[e_row, e_col]
        tp = target_arr[e_row, e_col]

        if np.isnan(e_price) or np.isnan(sl) or np.isnan(tp):
            entry_prices[t] = np.nan
            exit_prices[t] = np.nan
            exit_rows[t] = e_row
            pnl[t] = 0.0
            exit_reason_codes[t] = 0
            continue

        if long:
            e_price *= 1.0 + slippage
        else:
            e_price *= 1.0 - slippage
        entry_prices[t] = e_price

        start = e_row
        end = min(e_row + max_hold + 1, n_bars)
        if start >= end:
            exit_prices[t] = e_price
            exit_rows[t] = e_row
            pnl[t] = 0.0
            exit_reason_codes[t] = 8
            continue

        entry_date = dates_int[e_row]

        eod_idx = end - start
        for j in range(start, end):
            if dates_int[j] != entry_date:
                eod_idx = j - start
                break

        if eod_idx == 0:
            x_price = close[e_row, e_col]
            if long:
                x_price *= 1.0 - slippage
            else:
                x_price *= 1.0 + slippage
            exit_prices[t] = x_price
            exit_rows[t] = e_row
            exit_reason_codes[t] = 7
            if long:
                pnl[t] = (x_price - e_price) / e_price if e_price != 0 else 0.0
            else:
                pnl[t] = (e_price - x_price) / e_price if e_price != 0 else 0.0
            continue

        best_rel = eod_idx
        best_reason = 7
        hwm = e_price

        for j_rel in range(eod_idx):
            j = start + j_rel
            b_close = close[j, e_col]
            b_high = high[j, e_col]
            b_low = low[j, e_col]
            b_open = open_[j, e_col]

            if long:
                sl_hit = b_low <= sl
                tp_hit = b_high >= tp
                if sl_hit and tp_hit:
                    if abs(b_open - sl) <= abs(b_open - tp):
                        best_rel = j_rel
                        best_reason = 1
                    else:
                        best_rel = j_rel
                        best_reason = 2
                    break
                elif sl_hit:
                    best_rel = j_rel
                    best_reason = 1
                    break
                elif tp_hit:
                    best_rel = j_rel
                    best_reason = 2
                    break
                if b_high > hwm:
                    hwm = b_high
            else:
                sl_hit = b_high >= sl
                tp_hit = b_low <= tp
                if sl_hit and tp_hit:
                    if abs(b_open - sl) <= abs(b_open - tp):
                        best_rel = j_rel
                        best_reason = 1
                    else:
                        best_rel = j_rel
                        best_reason = 2
                    break
                elif sl_hit:
                    best_rel = j_rel
                    best_reason = 1
                    break
                elif tp_hit:
                    best_rel = j_rel
                    best_reason = 2
                    break
                if b_low < hwm:
                    hwm = b_low

            if use_time_stop and time_cutoff_mask[j]:
                best_rel = j_rel
                best_reason = 3
                break

            if use_failed_breakout:
                if long and b_close < or_high[j, e_col]:
                    best_rel = j_rel
                    best_reason = 4
                    break
                elif not long and b_close > or_low[j, e_col]:
                    best_rel = j_rel
                    best_reason = 4
                    break

            if use_vol_trail and has_atr:
                atr_val = atr[j, e_col]
                if not np.isnan(atr_val):
                    if long:
                        trail = hwm - vol_trail_mult * atr_val
                        if b_close < trail:
                            best_rel = j_rel
                            best_reason = 5
                            break
                    else:
                        trail = hwm + vol_trail_mult * atr_val
                        if b_close > trail:
                            best_rel = j_rel
                            best_reason = 5
                            break

            if use_opposite_break:
                if long and b_close < or_low[j, e_col]:
                    best_rel = j_rel
                    best_reason = 6
                    break
                elif not long and b_close > or_high[j, e_col]:
                    best_rel = j_rel
                    best_reason = 6
                    break

        if best_rel == eod_idx:
            if eod_idx < (end - start):
                best_rel = max(0, eod_idx - 1)
                best_reason = 7
            else:
                best_rel = eod_idx - 1
                best_reason = 8

        abs_bar = start + best_rel

        if best_reason == 2:
            x_price = tp
        elif best_reason == 1:
            if conservative_sl_fill:
                next_bar = abs_bar + 1
                if next_bar < n_bars and dates_int[next_bar] == entry_date:
                    x_price = open_[next_bar, e_col]
                    abs_bar = next_bar
                else:
                    x_price = close[abs_bar, e_col]
            else:
                x_price = sl
        else:
            x_price = close[abs_bar, e_col]

        if long:
            x_price *= 1.0 - slippage
        else:
            x_price *= 1.0 + slippage

        exit_prices[t] = x_price
        exit_rows[t] = abs_bar
        exit_reason_codes[t] = best_reason

        if long:
            pnl[t] = (x_price - e_price) / e_price if e_price != 0 else 0.0
        else:
            pnl[t] = (e_price - x_price) / e_price if e_price != 0 else 0.0

    return exit_rows, exit_prices, entry_prices, pnl, exit_reason_codes


EXIT_REASON_MAP = {
    0: "invalid",
    1: "stop_loss",
    2: "target",
    3: "time_stop",
    4: "failed_breakout",
    5: "vol_trail",
    6: "opposite_break",
    7: "eod",
    8: "timeout",
}


def apply_exits(
    close: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    open_: pd.DataFrame,
    or_high: pd.DataFrame,
    or_low: pd.DataFrame,
    entries_long: pd.DataFrame,
    entries_short: pd.DataFrame,
    stop_price: pd.DataFrame,
    target_price: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """Simulate trades using numba-JIT kernel."""
    # Defaults come from the one source (configs/orb_base.yaml via get_orb_defaults),
    # not re-declared literals — so the description, schema, and runtime cannot drift.
    defaults = get_orb_defaults()
    use_time_stop = config.get("use_time_stop", defaults["use_time_stop"])
    use_failed_breakout = config.get(
        "use_failed_breakout_exit", defaults["use_failed_breakout_exit"]
    )
    use_vol_trail = config.get("use_volatility_trail", defaults["use_volatility_trail"])
    use_opposite_break = config.get("use_opposite_break_exit", defaults["use_opposite_break_exit"])
    time_stop_hour = config.get("time_stop_hour", defaults["time_stop_hour"])
    time_stop_minute = config.get("time_stop_minute", defaults["time_stop_minute"])
    vol_trail_mult = config.get("vol_trail_atr_mult", defaults["vol_trail_atr_mult"])
    slippage = config.get("slippage_pct", defaults["slippage_pct"]) / 100.0
    max_hold = config.get("max_hold_bars", defaults["max_hold_bars"])
    conservative_sl_fill = config.get("conservative_sl_fill", defaults["conservative_sl_fill"])

    close_arr = close.values
    high_arr = high.values
    low_arr = low.values
    n_bars, n_syms = close_arr.shape
    index = close.index
    symbols = close.columns.tolist()

    # Date ordinals for day-boundary detection
    dates_int = np.array([d.toordinal() for d in index.date], dtype=np.int32)

    # Time cutoff mask
    if use_time_stop:
        cutoff = dt_time(time_stop_hour, time_stop_minute)
        time_cutoff_mask = np.array([t >= cutoff for t in index.time], dtype=np.bool_)
    else:
        time_cutoff_mask = np.zeros(n_bars, dtype=np.bool_)

    # ATR
    if use_vol_trail:
        atr_arr = compute_atr_nb(high_arr, low_arr, close_arr, 14)
    else:
        atr_arr = np.empty((0, 0), dtype=np.float64)

    # Collect entry coordinates
    long_rows, long_cols = np.where(entries_long.values)
    short_rows, short_cols = np.where(entries_short.values)
    n_long = len(long_rows)
    n_short = len(short_rows)
    n_total = n_long + n_short

    if n_total == 0:
        return pd.DataFrame(
            columns=[
                "symbol",
                "entry_date",
                "exit_date",
                "direction",
                "entry_price",
                "exit_price",
                "pnl_pct",
                "exit_reason",
            ]
        )

    entry_rows = np.concatenate([long_rows, short_rows]).astype(np.int64)
    entry_cols = np.concatenate([long_cols, short_cols]).astype(np.int64)
    is_long = np.concatenate([np.ones(n_long, dtype=np.bool_), np.zeros(n_short, dtype=np.bool_)])

    open_arr = open_.values

    # Run numba kernel
    exit_rows, exit_prices, entry_prices, pnl, reason_codes = simulate_trades(
        close_arr,
        high_arr,
        low_arr,
        open_arr,
        or_high.values,
        or_low.values,
        stop_price.values,
        target_price.values,
        entry_rows,
        entry_cols,
        is_long,
        dates_int,
        time_cutoff_mask,
        atr_arr,
        slippage,
        max_hold,
        use_time_stop,
        use_failed_breakout,
        use_vol_trail,
        use_opposite_break,
        vol_trail_mult,
        conservative_sl_fill,
    )

    # Map reason codes to strings
    reasons = np.array([EXIT_REASON_MAP.get(int(c), "unknown") for c in reason_codes])

    valid = ~np.isnan(entry_prices)

    trades_df = pd.DataFrame(
        {
            "symbol": [symbols[c] for c in entry_cols],
            "entry_date": index[entry_rows],
            "exit_date": index[np.clip(exit_rows, 0, n_bars - 1)],
            "direction": np.where(is_long, "long", "short"),
            "entry_price": entry_prices,
            "exit_price": exit_prices,
            "pnl_pct": pnl,
            "exit_reason": reasons,
        }
    )

    return trades_df[valid].reset_index(drop=True)
