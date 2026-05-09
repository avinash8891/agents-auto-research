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

    ema_length = int(config.get("ema_length", 5))
    ema = pd.Series(f_close).ewm(span=ema_length, adjust=False).mean().values

    signal_bars = np.flatnonzero(entry_mask)
    if len(signal_bars) == 0:
        return []

    trades: list[dict] = []
    in_trade_until = -1

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

        alert_bar_idx = None
        alert_bar_open = None
        alert_bar_close = None
        alert_bar_high = None
        alert_bar_low = None
        alert_bar_ema = None
        alert_bar_timestamp = None
        alert_close_minus_ema = None
        alert_close_leq_ema = None
        close_location_in_range = None
        upper_wick_share = None
        alert_candle_geometry_pass = None
        alert_body_pct = None
        alert_close_vs_ema_geometry_override_pass = None
        if hasattr(signals, "alert_bar_idx"):
            try:
                alert_bar_idx = int(np.asarray(signals.alert_bar_idx.values)[i])
            except Exception:
                alert_bar_idx = None
        if alert_bar_idx is not None and 0 <= alert_bar_idx < n:
            alert_bar_open = float(f_open[alert_bar_idx])
            alert_bar_close = float(f_close[alert_bar_idx])
            alert_bar_high = float(f_high[alert_bar_idx])
            alert_bar_low = float(f_low[alert_bar_idx])
            alert_bar_ema = float(ema[alert_bar_idx])
            alert_bar_timestamp = f_idx[alert_bar_idx]
            alert_close_minus_ema = alert_bar_close - alert_bar_ema
            alert_close_leq_ema = bool(alert_close_minus_ema <= 0)
            alert_range = alert_bar_high - alert_bar_low
            if alert_range > 0 and np.isfinite(alert_range):
                close_location_in_range = (alert_bar_close - alert_bar_low) / alert_range
                upper_wick_share = (alert_bar_high - max(alert_bar_open, alert_bar_close)) / alert_range
                alert_body_pct = abs(alert_bar_close - alert_bar_open) / alert_range

            override_cfg = (config or {}).get(
                "requires_engine_change__alert_close_vs_ema_secondary_geometry_override"
            )
            if isinstance(override_cfg, dict) and bool(override_cfg.get("enabled", False)):
                direction_cfg = override_cfg.get("direction", "short")
                if direction_cfg not in {"short", "long", "both"}:
                    direction_cfg = "short"
                eligible_dir = direction_cfg == "both" or direction_cfg == direction
                excluding = override_cfg.get("apply_to_entry_buckets_excluding", ["09:30"])
                if not (isinstance(excluding, list) and all(isinstance(x, str) for x in excluding)):
                    excluding = ["09:30"]
                try:
                    max_close_loc = float(override_cfg.get("require_close_location_in_range_leq", 1.0))
                except Exception:
                    max_close_loc = 1.0
                try:
                    entry_bucket = pd.Timestamp(f_idx[i]).strftime("%H:%M")
                except Exception:
                    entry_bucket = ""
                eligible_bucket = bool(entry_bucket) and entry_bucket not in set(excluding)
                override_when = bool(override_cfg.get("override_when_alert_close_gt_ema", True))
                if (
                    eligible_dir
                    and eligible_bucket
                    and override_when
                    and alert_close_minus_ema is not None
                    and close_location_in_range is not None
                    and np.isfinite(alert_close_minus_ema)
                    and np.isfinite(close_location_in_range)
                    and alert_close_minus_ema > 0
                ):
                    alert_close_vs_ema_geometry_override_pass = bool(close_location_in_range <= max_close_loc)
                else:
                    alert_close_vs_ema_geometry_override_pass = False

            body_cfg = (config or {}).get(
                "requires_engine_change__require_alert_candle_body_dominance_confirmation"
            )
            body_pass = None
            if isinstance(body_cfg, dict) and bool(body_cfg.get("enabled", False)):
                try:
                    min_body_pct = float(body_cfg.get("min_body_pct", 0.0))
                except Exception:
                    min_body_pct = 0.0
                if alert_body_pct is not None:
                    body_pass = bool(alert_body_pct >= min_body_pct)
            cfg = (config or {}).get(
                "requires_engine_change__require_alert_candle_close_location_confirmation"
            )
            close_loc_pass = None
            if isinstance(cfg, dict) and bool(cfg.get("enabled", False)):
                try:
                    max_close_loc = float(cfg.get("max_close_location_in_range", 1.0))
                except Exception:
                    max_close_loc = 1.0
                try:
                    max_upper_wick = float(cfg.get("max_upper_wick_share", 1.0))
                except Exception:
                    max_upper_wick = 1.0
                if close_location_in_range is not None and upper_wick_share is not None:
                    close_loc_pass = bool(
                        (close_location_in_range <= max_close_loc)
                        and (upper_wick_share <= max_upper_wick)
                    )
            if close_loc_pass is not None and body_pass is not None:
                alert_candle_geometry_pass = bool(close_loc_pass and body_pass)
            elif close_loc_pass is not None:
                alert_candle_geometry_pass = bool(close_loc_pass)
            elif body_pass is not None:
                alert_candle_geometry_pass = bool(body_pass)

        trades.append(
            {
                **(
                    {"trade_id": f"{symbol}|{direction}|{_ts(i)}|{int(i)}"}
                    if bool(config.get("requires_engine_change__persist_event_trade_join_key", False))
                    else {}
                ),
                "entry_date": f_idx[i],
                "exit_date": f_idx[exit_bar],
                "direction": direction,
                "entry_price": entry,
                "exit_price": exit_price,
                "stop": stop,
                "target": target if trail_after_r is None else trail_trigger,
                "pnl_pct": pnl,
                "exit_reason": exit_reason,
                "signal_bar_idx": int(i),
                "alert_bar_idx": alert_bar_idx,
                "alert_bar_timestamp": alert_bar_timestamp,
                "alert_bar_open": alert_bar_open,
                "alert_bar_close": alert_bar_close,
                "alert_bar_high": alert_bar_high,
                "alert_bar_low": alert_bar_low,
                "alert_bar_ema": alert_bar_ema,
                "alert_close_minus_ema": alert_close_minus_ema,
                "alert_close_leq_ema": alert_close_leq_ema,
                "close_location_in_range": close_location_in_range,
                "upper_wick_share": upper_wick_share,
                "alert_body_pct": alert_body_pct,
                "alert_candle_geometry_pass": alert_candle_geometry_pass,
                "alert_close_vs_ema_geometry_override_pass": alert_close_vs_ema_geometry_override_pass,
            }
        )

        if event_logger is not None:
            trade_id = None
            if bool(config.get("requires_engine_change__persist_event_trade_join_key", False)):
                trade_id = f"{symbol}|{direction}|{_ts(i)}|{int(i)}"
            alert_bar_range = np.nan
            lower_wick_share = np.nan
            if (
                alert_bar_high is not None
                and alert_bar_low is not None
                and alert_bar_open is not None
                and alert_bar_close is not None
            ):
                try:
                    rng = float(alert_bar_high) - float(alert_bar_low)
                except Exception:
                    rng = float("nan")
                if np.isfinite(rng) and rng > 0:
                    alert_bar_range = rng
                    try:
                        lower_wick_share = (min(float(alert_bar_open), float(alert_bar_close)) - float(alert_bar_low)) / rng
                    except Exception:
                        lower_wick_share = np.nan
            extras = {
                # Cohort-compare executed trades vs rejected signals in strategy_events.
                "alert_bar_range": alert_bar_range,
                "upper_wick_share": float(upper_wick_share) if upper_wick_share is not None else np.nan,
                "lower_wick_share": lower_wick_share,
                "close_location_in_range": float(close_location_in_range) if close_location_in_range is not None else np.nan,
                "alert_close_minus_ema": float(alert_close_minus_ema) if alert_close_minus_ema is not None else np.nan,
                "alert_body_pct": float(alert_body_pct) if alert_body_pct is not None else np.nan,
            }
            try:
                extras["trigger_bucket"] = pd.Timestamp(f_idx[i]).strftime("%H:%M")
            except Exception:
                extras["trigger_bucket"] = ""
            try:
                extras["alert_bucket"] = (
                    pd.Timestamp(alert_bar_timestamp).strftime("%H:%M") if alert_bar_timestamp is not None else ""
                )
            except Exception:
                extras["alert_bucket"] = ""
            if trade_id is not None:
                extras["trade_id"] = trade_id
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
                extras=extras,
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
