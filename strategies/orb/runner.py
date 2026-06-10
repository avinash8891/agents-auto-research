"""ORB strategy runtime.

The CLI and artifact writer live in the generic `backtest.runner` path; this
module only owns ORB-specific backtest behavior.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from backtest.data_universe import load_universe_data
from metrics import compute_metrics, empty_metrics
from strategy_event_logger import StrategyEventLogger


def run_backtest(config: dict) -> dict:
    """Run ORB backtest on the validation period. Returns metrics dict."""
    from strategies.orb.exits import apply_exits
    from strategies.orb.regime_filter import (
        apply_regime_gate,
        classify_regimes,
        compute_regime_expectancy,
    )
    from strategies.orb.signals import generate_orb_signals

    config = dict(config)
    config.setdefault("validation_start", "2020-01-01")
    config.setdefault("validation_end", "2023-12-31")

    batch = load_universe_data(config)
    open_ = batch["open"]
    high = batch["high"]
    low = batch["low"]
    close = batch["close"]
    volume = batch.get("volume")

    print(f"Data loaded: {close.shape[0]} bars, {close.shape[1]} symbols", file=sys.stderr)

    if close.empty:
        return {**empty_metrics(), "_trades_df": pd.DataFrame(), "_event_logger": None}

    print(f"Date range: {close.index[0]} to {close.index[-1]}", file=sys.stderr)

    universe_mode = config.get("universe_mode")
    if universe_mode == "stocks_in_play":
        top_n = int(config.get("stocks_in_play_top_n", 20))
        ranking = config.get("stocks_in_play_ranking", "first30_dollar_volume")
        open_, high, low, close, volume = _apply_stocks_in_play_filter(
            open_, high, low, close, volume, top_n=top_n, ranking=ranking
        )
        if close.empty or close.dropna(how="all").empty:
            return {**empty_metrics(), "_trades_df": pd.DataFrame(), "_event_logger": None}

    event_logger = StrategyEventLogger()
    signals = generate_orb_signals(open_, high, low, close, volume=volume, config=config)
    _log_signal_events(
        event_logger,
        signals,
        close,
        config,
    )

    regime_labels = classify_regimes(
        open_,
        high,
        low,
        close,
        or_high=signals.or_high,
        or_low=signals.or_low,
        wide_or_mult=config.get("wide_or_mult", 1.5),
        narrow_or_mult=config.get("narrow_or_mult", 0.5),
    )

    trades_df = apply_exits(
        close,
        high,
        low,
        open_,
        signals.or_high,
        signals.or_low,
        signals.entries_long,
        signals.entries_short,
        signals.stop_price,
        signals.target_price,
        config,
    )

    skip_regimes = set(config.get("skip_regimes", []))
    require_regimes = set(config.get("require_regimes", []))
    pre_gate_count = len(trades_df)
    if skip_regimes or require_regimes:
        trades_df = apply_regime_gate(
            trades_df,
            regime_labels,
            skip_regimes=skip_regimes or None,
            require_regimes=require_regimes or None,
        )
    gated_count = pre_gate_count - len(trades_df)

    if gated_count > 0:
        event_logger.log(
            timestamp="",
            symbol="",
            direction="",
            event_type="regime_gate",
            status="rejected",
            stage="filter_evaluation",
            reason=f"skip={list(skip_regimes)},require={list(require_regimes)}",
            filter_value=str(gated_count),
        )

    if trades_df.empty or trades_df["pnl_pct"].isna().all():
        return {**empty_metrics(), "_trades_df": trades_df, "_event_logger": event_logger}

    trades_df = (
        trades_df[trades_df["pnl_pct"].notna()].sort_values("entry_date").reset_index(drop=True)
    )

    if "stop" not in trades_df.columns:
        trades_df["stop"] = np.nan
    if "target" not in trades_df.columns:
        trades_df["target"] = np.nan

    result = compute_metrics(trades_df)
    regime_exp = compute_regime_expectancy(trades_df, regime_labels)
    result["regime_expectancy"] = regime_exp

    if not trades_df.empty:
        et = trades_df[["entry_date", "symbol", "direction", "entry_price"]].copy()
        et = et.rename(columns={"entry_date": "timestamp"})
        et["event_type"] = "executed_trade"
        et["reason"] = (
            trades_df["exit_reason"].astype(str) if "exit_reason" in trades_df.columns else ""
        )
        et["stop_price"] = trades_df["stop"] if "stop" in trades_df.columns else np.nan
        event_logger.record_dataframe(et, "executed_trade")

    result["_trades_df"] = trades_df
    result["_event_logger"] = event_logger
    return result


def _log_signal_events(
    logger: StrategyEventLogger,
    signals,
    close: pd.DataFrame,
    config: dict,
) -> None:
    """Log signal generation events for the research analyst."""
    long_count = int(signals.entries_long.values.sum())
    short_count = int(signals.entries_short.values.sum())
    total_bars = close.shape[0] * close.shape[1]

    logger.log(
        timestamp="",
        symbol="",
        direction="",
        event_type="raw_setup",
        status="scanned",
        stage="signal_generation",
        filter_value=str(total_bars),
        reason=f"total_bar_symbol_pairs={total_bars}",
    )
    logger.log(
        timestamp="",
        symbol="",
        direction="",
        event_type="accepted_signal",
        status="accepted",
        stage="signal_generation",
        filter_value=str(long_count + short_count),
        reason=f"long={long_count},short={short_count}",
    )


def _apply_stocks_in_play_filter(
    open_: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    volume: pd.DataFrame | None,
    top_n: int = 20,
    ranking: str = "first30_dollar_volume",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """Keep only top-N active names per day, masking all others to NaN."""
    if volume is None or close.empty:
        return open_, high, low, close, volume

    idx = close.index
    normalized_idx = idx.normalize()
    days = pd.Index(normalized_idx.unique())
    bars_in_or = 6  # 30 min / 5 min
    mask = pd.DataFrame(False, index=idx, columns=close.columns)

    if ranking == "first30_relative_volume":
        daily_first30 = []
        for day in days:
            day_rows = normalized_idx == day
            day_idx = np.where(day_rows)[0]
            if len(day_idx) < bars_in_or:
                vals = pd.Series(np.nan, index=close.columns)
            else:
                first = day_idx[:bars_in_or]
                vals = volume.iloc[first].sum(axis=0)
            vals.name = day
            daily_first30.append(vals)
        first30_daily = pd.DataFrame(daily_first30)
        scores_df = first30_daily / first30_daily.shift(1).rolling(20, min_periods=5).mean()
    else:
        scores_df = None

    for day in days:
        day_rows = normalized_idx == day
        day_idx = np.where(day_rows)[0]
        if len(day_idx) < bars_in_or:
            continue
        if ranking == "first30_relative_volume":
            scores = scores_df.loc[day].replace([np.inf, -np.inf], np.nan).dropna()
        else:
            first = day_idx[:bars_in_or]
            scores = (close.iloc[first] * volume.iloc[first]).sum(axis=0).dropna()
        if scores.empty:
            continue
        top = scores.nlargest(min(top_n, len(scores))).index
        mask.loc[idx[day_rows], top] = True

    return (
        open_.where(mask),
        high.where(mask),
        low.where(mask),
        close.where(mask),
        volume.where(mask) if volume is not None else None,
    )
