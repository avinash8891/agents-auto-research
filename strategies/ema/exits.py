"""Exit simulation for 5 EMA strategy.

Takes a resampled OHLC frame and EMASignals, simulates trades with
fixed stop/target exits plus slippage. Returns a list of trade dicts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from strategy_event_logger import StrategyEventLogger


def simulate_trades(
    frame: pd.DataFrame,
    signals,  # EMASignals from strategies.ema.signals
    config: dict,
    symbol: str = "",
    event_logger: "StrategyEventLogger | None" = None,
) -> list[dict]:
    """Simulate trades u2014 vectorized exit scanning with numpy.

    Supports two exit modes:
      1. Fixed target (default): exit at exactly rr_ratio u00d7 risk.
      2. Trail after R (transcript-faithful): once price reaches trail_after_r
         multiples favorable, switch to candle-by-candle trailing stop.
         Trail method: previous bar's high (shorts) or low (longs).
    """
    rr_ratio = float(config.get("rr_ratio", 3.0))
    max_hold = int(config.get("max_hold_bars", 78))
    slippage = config.get("slippage_pct", 0.05) / 100.0
    direction = signals.direction
    is_long = direction == "long"

    # Trailing config: trail_after_r = None means fixed target (legacy behavior)
    # trail_after_r = 3.0 means: once price moves 3R favorable, trail candle-wise
    trail_after_r = config.get("trail_after_r", None)
    if trail_after_r is not None:
        trail_after_r = float(trail_after_r)

    entry_mask = signals.entries.values
    raw_entry_prices = signals.entry_price.values
    raw_stop_prices = signals.stop_price.values

    f_high = frame["high"].values
    f_low = frame["low"].values
    f_open = frame["open"].values
    f_close = frame["close"].values
    f_idx = frame.index
    n = len(frame)

    signal_bars = np.flatnonzero(entry_mask)
    if len(signal_bars) == 0:
        return []

    trades: list[dict] = []
    in_trade_until = -1
    alert_bar_idx = getattr(signals, "alert_bar_idx", pd.Series(np.full(n, -1, dtype=int)))
    alert_bar_idx_values = pd.Series(alert_bar_idx).to_numpy(dtype=int, copy=False)
    if isinstance(f_idx, pd.DatetimeIndex) and len(f_idx):
        day_values = f_idx.normalize()
        entry_bar_index_from_open = np.zeros(n, dtype=np.int64)
        current = 0
        for idx in range(1, n):
            if day_values[idx] == day_values[idx - 1]:
                current += 1
            else:
                current = 0
            entry_bar_index_from_open[idx] = current
    else:
        entry_bar_index_from_open = np.arange(n, dtype=np.int64)

    def _ts(bar_idx: int) -> str:
        return str(f_idx[bar_idx]) if bar_idx < n else ""

    def _log_rejection(
        bar_idx: int, reason: str, ep: float = float("nan"), sp: float = float("nan")
    ) -> None:
        if event_logger is None:
            return
        event_logger.log(
            timestamp=_ts(bar_idx),
            symbol=symbol,
            direction=direction,
            event_type="order_rejected",
            status="rejected",
            stage="execution_simulation",
            reason=reason,
            entry_price=ep if ep == ep else None,
            stop_price=sp if sp == sp else None,
        )

    for i in signal_bars:
        if i <= in_trade_until:
            _log_rejection(i, "position_already_open", raw_entry_prices[i], raw_stop_prices[i])
            continue

        entry_raw = raw_entry_prices[i]
        stop = raw_stop_prices[i]
        if entry_raw != entry_raw or stop != stop or entry_raw <= 0:  # NaN check
            _log_rejection(i, "invalid_price")
            continue

        entry = entry_raw * (1.0 + slippage) if is_long else entry_raw * (1.0 - slippage)

        if is_long and stop >= entry:
            _log_rejection(i, "inverted_risk", entry, stop)
            continue
        if not is_long and stop <= entry:
            _log_rejection(i, "inverted_risk", entry, stop)
            continue

        risk = abs(entry - stop)
        if risk <= 0:
            _log_rejection(i, "zero_risk", entry, stop)
            continue

        target = entry + rr_ratio * risk if is_long else entry - rr_ratio * risk

        # --- Exit simulation ---
        if trail_after_r is None:
            # Legacy: fixed target exit
            exit_bar, exit_price, exit_reason = _exit_fixed_target(
                i,
                n,
                entry,
                stop,
                target,
                risk,
                is_long,
                max_hold,
                f_high,
                f_low,
                f_open,
                f_close,
            )
        else:
            # Transcript-faithful: trail after reaching minimum R
            trail_trigger = (
                entry + trail_after_r * risk if is_long else entry - trail_after_r * risk
            )
            exit_bar, exit_price, exit_reason = _exit_trail_after_r(
                i,
                n,
                entry,
                stop,
                trail_trigger,
                risk,
                is_long,
                max_hold,
                f_high,
                f_low,
                f_open,
                f_close,
            )

        if exit_bar < 0:
            continue

        if is_long:
            exit_price *= 1.0 - slippage
            pnl = (exit_price - entry) / entry
        else:
            exit_price *= 1.0 + slippage
            pnl = (entry - exit_price) / entry

        in_trade_until = exit_bar

        trades.append(
            {
                "entry_date": f_idx[i],
                "exit_date": f_idx[exit_bar],
                "direction": direction,
                "entry_price": entry,
                "exit_price": exit_price,
                "stop": stop,
                "target": target if trail_after_r is None else trail_trigger,
                "pnl_pct": pnl,
                "exit_reason": exit_reason,
            }
        )

        if event_logger is not None:
            event_logger.log(
                timestamp=_ts(i),
                symbol=symbol,
                direction=direction,
                event_type="executed_trade",
                status="executed",
                stage="execution_simulation",
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                rr_ratio=rr_ratio,
                **{
                    "ema.alert_bar_timestamp": (
                        _ts(alert_bar_idx_values[i]) if 0 <= alert_bar_idx_values[i] < n else None
                    )
                },
                trigger_bar_timestamp=_ts(i),
                entry_bar_index_from_open=int(entry_bar_index_from_open[i]),
            )

    return trades


def _exit_fixed_target(
    i: int,
    n: int,
    entry: float,
    stop: float,
    target: float,
    risk: float,
    is_long: bool,
    max_hold: int,
    f_high: np.ndarray,
    f_low: np.ndarray,
    f_open: np.ndarray,
    f_close: np.ndarray,
) -> tuple[int, float, str]:
    """Original fixed-target exit logic."""
    end = min(i + max_hold + 1, n)
    sl = slice(i + 1, end)
    h_chunk = f_high[sl]
    l_chunk = f_low[sl]
    chunk_len = len(h_chunk)
    if chunk_len == 0:
        return -1, 0.0, ""

    if is_long:
        sl_hits = l_chunk <= stop
        tp_hits = h_chunk >= target
    else:
        sl_hits = h_chunk >= stop
        tp_hits = l_chunk <= target

    sl_first = np.argmax(sl_hits) if sl_hits.any() else chunk_len
    tp_first = np.argmax(tp_hits) if tp_hits.any() else chunk_len
    if not sl_hits.any():
        sl_first = chunk_len
    if not tp_hits.any():
        tp_first = chunk_len

    if sl_first == chunk_len and tp_first == chunk_len:
        exit_bar = end - 1
        exit_price = f_close[exit_bar]
        exit_reason = "timeout"
    elif sl_first < tp_first:
        exit_bar = i + 1 + sl_first
        exit_price = stop
        exit_reason = "stop_loss"
    elif tp_first < sl_first:
        exit_bar = i + 1 + tp_first
        exit_price = target
        exit_reason = "target"
    else:
        j = i + 1 + sl_first
        if abs(f_open[j] - stop) <= abs(f_open[j] - target):
            exit_price = stop
            exit_reason = "stop_loss"
        else:
            exit_price = target
            exit_reason = "target"
        exit_bar = j

    return exit_bar, exit_price, exit_reason


def _exit_trail_after_r(
    i: int,
    n: int,
    entry: float,
    stop: float,
    trail_trigger: float,
    risk: float,
    is_long: bool,
    max_hold: int,
    f_high: np.ndarray,
    f_low: np.ndarray,
    f_open: np.ndarray,
    f_close: np.ndarray,
) -> tuple[int, float, str]:
    """Transcript-faithful exit: fixed stop until trail_trigger reached,
    then trail candle-by-candle.

    Trail logic (from Subashish):
      - Short: once price drops to trail_trigger, move stop to previous bar's high.
        Each subsequent bar, tighten stop to prev bar's high if lower.
      - Long: mirror with previous bar's low.
    """
    end = min(i + max_hold + 1, n)
    trailing = False
    current_stop = stop

    for j in range(i + 1, end):
        bar_high = f_high[j]
        bar_low = f_low[j]

        # Check stop hit first
        if is_long:
            if bar_low <= current_stop:
                return j, current_stop, "trail_stop" if trailing else "stop_loss"
        else:
            if bar_high >= current_stop:
                return j, current_stop, "trail_stop" if trailing else "stop_loss"

        # Check if trail should activate
        if not trailing:
            if is_long and bar_high >= trail_trigger:
                trailing = True
            elif not is_long and bar_low <= trail_trigger:
                trailing = True

        # Update trailing stop (candle-wise: previous bar's extreme)
        if trailing:
            if is_long:
                # Trail to previous bar's low (tighten only)
                candidate = f_low[j - 1] if j > i + 1 else f_low[i]
                if candidate > current_stop:
                    current_stop = candidate
            else:
                # Trail to previous bar's high (tighten only)
                candidate = f_high[j - 1] if j > i + 1 else f_high[i]
                if candidate < current_stop:
                    current_stop = candidate

    # Timeout: held to max_hold without stop hit
    exit_bar = end - 1
    return exit_bar, f_close[exit_bar], "timeout"
