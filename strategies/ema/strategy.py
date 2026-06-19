from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest.data_universe import load_universe_data
from backtest.filters import (
    _compute_gap_down_days,
    _compute_gap_up_days,
    _exclude_signals_before_bar,
    _exclude_signals_on_days,
    _filter_signals_to_days,
)
from backtest.resample import build_timeframe_frame
from metrics import compute_metrics, empty_metrics
from strategies.base import BaseStrategy
from strategies.contract import validate_backtest_runtime_config
from strategies.ema.contract import compile_ema_contract, map_ema_config_changes_to_contract
from strategies.ema.exits import simulate_trades
from strategies.ema.prompt import DESCRIPTION_FOR_RESEARCH
from strategies.ema.research import EMA_RESEARCH_SPEC
from strategies.ema.validate import validate_ema_runtime_config
from strategy_event_logger import StrategyEventLogger


def _log_filter_rejections(
    event_logger: StrategyEventLogger,
    frame: pd.DataFrame,
    signals,
    before_mask: np.ndarray,
    after_mask: np.ndarray,
    entry_prices: np.ndarray,
    stop_prices: np.ndarray,
    symbol: str,
    direction: str,
    reason: str,
) -> None:
    killed = before_mask & ~after_mask
    extras_signals = signals
    if killed.any():
        from dataclasses import replace

        extras_signals = replace(
            signals,
            entry_price=pd.Series(entry_prices, index=frame.index),
            stop_price=pd.Series(stop_prices, index=frame.index),
        )
    event_logger.record_events(
        timestamps=frame.index,
        mask=killed,
        symbol=symbol,
        direction=direction,
        event_type="rejected_signal",
        reason=reason,
        entry_prices=entry_prices,
        stop_prices=stop_prices,
        extras=_standard_event_extras(frame, extras_signals),
    )


def _log_raw_setups(
    event_logger: StrategyEventLogger,
    frame: pd.DataFrame,
    signals,
    symbol: str,
    direction: str,
) -> None:
    event_logger.record_events(
        timestamps=frame.index,
        mask=signals.entries.values,
        symbol=symbol,
        direction=direction,
        event_type="raw_setup",
        entry_prices=signals.entry_price.values,
        stop_prices=signals.stop_price.values,
        extras=_standard_event_extras(frame, signals),
    )


def _log_accepted_signals(
    event_logger: StrategyEventLogger,
    frame: pd.DataFrame,
    signals,
    symbol: str,
    direction: str,
) -> None:
    event_logger.record_events(
        timestamps=frame.index,
        mask=signals.entries.values,
        symbol=symbol,
        direction=direction,
        event_type="accepted_signal",
        entry_prices=signals.entry_price.values,
        stop_prices=signals.stop_price.values,
        extras=_standard_event_extras(frame, signals),
    )


def _entry_bar_index_from_open(index: pd.Index) -> np.ndarray:
    n = len(index)
    if not isinstance(index, pd.DatetimeIndex) or n == 0:
        return np.arange(n, dtype=np.int64)
    values = np.zeros(n, dtype=np.int64)
    days = index.normalize()
    current = 0
    values[0] = 0
    for i in range(1, n):
        if days[i] == days[i - 1]:
            current += 1
        else:
            current = 0
        values[i] = current
    return values


def _standard_event_extras(frame: pd.DataFrame, signals) -> dict[str, np.ndarray]:
    n = len(frame)
    if n == 0:
        return {}

    stop_distance_pct = np.full(n, np.nan, dtype=np.float64)
    entry_values = signals.entry_price.to_numpy(dtype=float, copy=False)
    stop_values = signals.stop_price.to_numpy(dtype=float, copy=False)
    valid_prices = np.isfinite(entry_values) & np.isfinite(stop_values) & (entry_values > 0)
    stop_distance_pct[valid_prices] = (
        np.abs(entry_values[valid_prices] - stop_values[valid_prices])
        / entry_values[valid_prices]
        * 100.0
    )

    trigger_bar_timestamp = (
        frame.index.to_numpy(dtype="datetime64[ns]")
        if isinstance(frame.index, pd.DatetimeIndex)
        else np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
    )
    ema_alert_bar_timestamp = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
    alert_idx = getattr(signals, "alert_bar_idx", None)
    if alert_idx is not None and isinstance(frame.index, pd.DatetimeIndex):
        alert_idx_values = pd.Series(alert_idx).to_numpy(dtype=int, copy=False)
        valid_alert_idx = (alert_idx_values >= 0) & (alert_idx_values < n)
        ema_alert_bar_timestamp[valid_alert_idx] = trigger_bar_timestamp[
            alert_idx_values[valid_alert_idx]
        ]

    return {
        "stop_distance_pct": stop_distance_pct,
        "trigger_bar_timestamp": trigger_bar_timestamp,
        "entry_bar_index_from_open": _entry_bar_index_from_open(frame.index),
        "ema.alert_bar_timestamp": ema_alert_bar_timestamp,
    }


def run_backtest(config: dict) -> dict:
    from strategies.ema.signals import generate_signals_for_frame

    config = validate_backtest_runtime_config("ema", dict(config))
    event_logger = StrategyEventLogger()

    batch = load_universe_data(config)
    close = batch["close"]
    high = batch["high"]
    low = batch["low"]
    open_ = batch["open"]
    if close.empty:
        return empty_metrics()

    ema_length = int(config["ema_length"])
    tf_long = int(config["timeframe_long"])
    tf_short = int(config["timeframe_short"])
    direction_bias = config.get("direction_bias", "long_only")
    use_range_shift = config.get("use_range_shift", False)
    range_shift_lookback = int(config.get("range_shift_lookback", 20))
    entry_cutoff_time = config.get("entry_cutoff_time", None)
    gap_filter = config.get("gap_filter", False)
    gap_pct = config.get("gap_pct", 0.01)
    gap_exclude = config.get("gap_exclude", False)
    gap_exclude_pct = config.get("gap_exclude_pct", 0.005)
    gap_exclude_direction = config.get("gap_exclude_direction", "up")
    # D4 lever: exclude entries in the first N bars of each day (bars_since_open
    # < N). 0 = off. Lets "skip first-post-open entries" be a validated config.
    exclude_first_bars = int(config.get("exclude_first_bars", 0) or 0)
    min_stop_distance_pct = config.get("min_stop_distance_pct", None)
    if min_stop_distance_pct is not None:
        min_stop_distance_pct = float(min_stop_distance_pct)
    max_stop_distance_pct = config.get("max_stop_distance_pct", None)
    if max_stop_distance_pct is not None:
        max_stop_distance_pct = float(max_stop_distance_pct)
    # Transcript: 3-5 trades per day TOTAL (T2: "five trades"; T4: "not more than 3 times a day")
    max_trades_per_day = config.get("max_trades_per_day", None)
    entry_cutoff = pd.Timestamp(entry_cutoff_time).time() if entry_cutoff_time else None

    all_trades: list[dict] = []

    for symbol in close.columns:
        sym_open = open_[symbol].dropna()
        sym_close = close[symbol].dropna()
        sym_high = high[symbol].dropna()
        sym_low = low[symbol].dropna()

        if sym_close.empty:
            continue

        gap_up_days = _compute_gap_up_days(sym_open, sym_close, gap_pct) if gap_filter else None
        gap_down_days = _compute_gap_down_days(sym_open, sym_close, gap_pct) if gap_filter else None
        # Signed gap exclusion: "up" excludes gap-up days, "down" excludes gap-down.
        if gap_exclude:
            gap_exclude_compute = (
                _compute_gap_down_days if gap_exclude_direction == "down" else _compute_gap_up_days
            )
            gap_exclude_days = gap_exclude_compute(sym_open, sym_close, gap_exclude_pct)
        else:
            gap_exclude_days = None

        if direction_bias in {"both", "long_only"}:
            long_frame = build_timeframe_frame(sym_open, sym_close, sym_high, sym_low, tf_long)
            long_signals = generate_signals_for_frame(
                long_frame,
                "long",
                ema_length,
                use_range_shift=use_range_shift,
                range_shift_lookback=range_shift_lookback,
            )

            _log_raw_setups(event_logger, long_frame, long_signals, symbol, "long")

            if entry_cutoff and isinstance(long_frame.index, pd.DatetimeIndex):
                before = long_signals.entries.values.copy()
                entry_before = long_signals.entry_price.values.copy()
                stop_before = long_signals.stop_price.values.copy()
                mask = long_frame.index.time <= entry_cutoff
                long_signals.entries[~mask] = False
                long_signals.entry_price[~mask] = np.nan
                long_signals.stop_price[~mask] = np.nan
                _log_filter_rejections(
                    event_logger,
                    long_frame,
                    long_signals,
                    before,
                    long_signals.entries.values,
                    entry_before,
                    stop_before,
                    symbol,
                    "long",
                    "entry_cutoff",
                )
            if exclude_first_bars > 0:
                before = long_signals.entries.values.copy()
                entry_before = long_signals.entry_price.values.copy()
                stop_before = long_signals.stop_price.values.copy()
                _exclude_signals_before_bar(long_signals, long_frame, exclude_first_bars)
                _log_filter_rejections(
                    event_logger,
                    long_frame,
                    long_signals,
                    before,
                    long_signals.entries.values,
                    entry_before,
                    stop_before,
                    symbol,
                    "long",
                    "exclude_first_bars",
                )
            if gap_filter:
                before = long_signals.entries.values.copy()
                entry_before = long_signals.entry_price.values.copy()
                stop_before = long_signals.stop_price.values.copy()
                _filter_signals_to_days(long_signals, long_frame, gap_down_days)
                _log_filter_rejections(
                    event_logger,
                    long_frame,
                    long_signals,
                    before,
                    long_signals.entries.values,
                    entry_before,
                    stop_before,
                    symbol,
                    "long",
                    "gap_filter",
                )

            if min_stop_distance_pct is not None or max_stop_distance_pct is not None:
                long_stop_dist = (
                    np.abs(long_signals.entry_price - long_signals.stop_price)
                    / long_signals.entry_price
                    * 100
                )
                if min_stop_distance_pct is not None:
                    before = long_signals.entries.values.copy()
                    entry_before = long_signals.entry_price.values.copy()
                    stop_before = long_signals.stop_price.values.copy()
                    long_signals.entries[long_stop_dist < min_stop_distance_pct] = False
                    long_signals.entry_price[long_stop_dist < min_stop_distance_pct] = np.nan
                    long_signals.stop_price[long_stop_dist < min_stop_distance_pct] = np.nan
                    _log_filter_rejections(
                        event_logger,
                        long_frame,
                        long_signals,
                        before,
                        long_signals.entries.values,
                        entry_before,
                        stop_before,
                        symbol,
                        "long",
                        "min_stop_distance",
                    )
                if max_stop_distance_pct is not None:
                    before = long_signals.entries.values.copy()
                    entry_before = long_signals.entry_price.values.copy()
                    stop_before = long_signals.stop_price.values.copy()
                    long_signals.entries[long_stop_dist > max_stop_distance_pct] = False
                    long_signals.entry_price[long_stop_dist > max_stop_distance_pct] = np.nan
                    long_signals.stop_price[long_stop_dist > max_stop_distance_pct] = np.nan
                    _log_filter_rejections(
                        event_logger,
                        long_frame,
                        long_signals,
                        before,
                        long_signals.entries.values,
                        entry_before,
                        stop_before,
                        symbol,
                        "long",
                        "max_stop_distance",
                    )

            _log_accepted_signals(event_logger, long_frame, long_signals, symbol, "long")

            for t in simulate_trades(
                long_frame, long_signals, config, symbol=symbol, event_logger=event_logger
            ):
                t["symbol"] = symbol
                all_trades.append(t)

        if direction_bias in {"both", "short_only"}:
            short_frame = build_timeframe_frame(sym_open, sym_close, sym_high, sym_low, tf_short)
            short_signals = generate_signals_for_frame(
                short_frame,
                "short",
                ema_length,
                use_range_shift=use_range_shift,
                range_shift_lookback=range_shift_lookback,
            )

            _log_raw_setups(event_logger, short_frame, short_signals, symbol, "short")

            if entry_cutoff and isinstance(short_frame.index, pd.DatetimeIndex):
                before = short_signals.entries.values.copy()
                entry_before = short_signals.entry_price.values.copy()
                stop_before = short_signals.stop_price.values.copy()
                mask = short_frame.index.time <= entry_cutoff
                short_signals.entries[~mask] = False
                short_signals.entry_price[~mask] = np.nan
                short_signals.stop_price[~mask] = np.nan
                _log_filter_rejections(
                    event_logger,
                    short_frame,
                    short_signals,
                    before,
                    short_signals.entries.values,
                    entry_before,
                    stop_before,
                    symbol,
                    "short",
                    "entry_cutoff",
                )
            if exclude_first_bars > 0:
                before = short_signals.entries.values.copy()
                entry_before = short_signals.entry_price.values.copy()
                stop_before = short_signals.stop_price.values.copy()
                _exclude_signals_before_bar(short_signals, short_frame, exclude_first_bars)
                _log_filter_rejections(
                    event_logger,
                    short_frame,
                    short_signals,
                    before,
                    short_signals.entries.values,
                    entry_before,
                    stop_before,
                    symbol,
                    "short",
                    "exclude_first_bars",
                )
            if gap_filter:
                # Short on gap-up days (fade the gap — transcript core rule)
                before = short_signals.entries.values.copy()
                entry_before = short_signals.entry_price.values.copy()
                stop_before = short_signals.stop_price.values.copy()
                _filter_signals_to_days(short_signals, short_frame, gap_up_days)
                _log_filter_rejections(
                    event_logger,
                    short_frame,
                    short_signals,
                    before,
                    short_signals.entries.values,
                    entry_before,
                    stop_before,
                    symbol,
                    "short",
                    "gap_filter",
                )

            if gap_exclude and gap_exclude_days:
                before = short_signals.entries.values.copy()
                entry_before = short_signals.entry_price.values.copy()
                stop_before = short_signals.stop_price.values.copy()
                _exclude_signals_on_days(short_signals, short_frame, gap_exclude_days)
                _log_filter_rejections(
                    event_logger,
                    short_frame,
                    short_signals,
                    before,
                    short_signals.entries.values,
                    entry_before,
                    stop_before,
                    symbol,
                    "short",
                    "gap_exclude",
                )

            if min_stop_distance_pct is not None or max_stop_distance_pct is not None:
                short_stop_dist = (
                    np.abs(short_signals.entry_price - short_signals.stop_price)
                    / short_signals.entry_price
                    * 100
                )
                if min_stop_distance_pct is not None:
                    before = short_signals.entries.values.copy()
                    entry_before = short_signals.entry_price.values.copy()
                    stop_before = short_signals.stop_price.values.copy()
                    short_signals.entries[short_stop_dist < min_stop_distance_pct] = False
                    short_signals.entry_price[short_stop_dist < min_stop_distance_pct] = np.nan
                    short_signals.stop_price[short_stop_dist < min_stop_distance_pct] = np.nan
                    _log_filter_rejections(
                        event_logger,
                        short_frame,
                        short_signals,
                        before,
                        short_signals.entries.values,
                        entry_before,
                        stop_before,
                        symbol,
                        "short",
                        "min_stop_distance",
                    )
                if max_stop_distance_pct is not None:
                    before = short_signals.entries.values.copy()
                    entry_before = short_signals.entry_price.values.copy()
                    stop_before = short_signals.stop_price.values.copy()
                    short_signals.entries[short_stop_dist > max_stop_distance_pct] = False
                    short_signals.entry_price[short_stop_dist > max_stop_distance_pct] = np.nan
                    short_signals.stop_price[short_stop_dist > max_stop_distance_pct] = np.nan
                    _log_filter_rejections(
                        event_logger,
                        short_frame,
                        short_signals,
                        before,
                        short_signals.entries.values,
                        entry_before,
                        stop_before,
                        symbol,
                        "short",
                        "max_stop_distance",
                    )

            _log_accepted_signals(event_logger, short_frame, short_signals, symbol, "short")

            for t in simulate_trades(
                short_frame, short_signals, config, symbol=symbol, event_logger=event_logger
            ):
                t["symbol"] = symbol
                all_trades.append(t)

    if not all_trades:
        result = empty_metrics()
        result["_event_logger"] = event_logger
        return result

    trades_df = pd.DataFrame(all_trades)

    if max_trades_per_day and max_trades_per_day > 0:
        trades_df = trades_df.sort_values("entry_date")
        trades_df["_date"] = trades_df["entry_date"].dt.date
        kept = trades_df.groupby("_date").head(max_trades_per_day)
        rejected_idx = trades_df.index.difference(kept.index)
        if len(rejected_idx):
            rej = trades_df.loc[
                rejected_idx, ["entry_date", "symbol", "direction", "entry_price", "stop"]
            ].copy()
            rej = rej.rename(columns={"entry_date": "timestamp", "stop": "stop_price"})
            rej["event_type"] = "rejected_signal"
            rej["reason"] = "max_trades_per_day"
            event_logger.record_dataframe(rej, "rejected_signal", "max_trades_per_day")
        trades_df = kept.drop(columns=["_date"])

    result = compute_metrics(trades_df)
    result["_trades_df"] = trades_df
    result["_event_logger"] = event_logger
    return result


class EMAStrategy(BaseStrategy):
    name = "ema"
    benchmark_script = "backtest_5ema.py"
    description_for_research = DESCRIPTION_FOR_RESEARCH
    research_spec = EMA_RESEARCH_SPEC

    def run(self, config: dict[str, Any]) -> dict[str, Any]:
        return run_backtest(config)

    def validate_runtime_config(self, config: dict[str, Any]) -> list[str]:
        return validate_ema_runtime_config(config)

    def compile_contract(self, contract: list[dict[str, Any]]):
        return compile_ema_contract(contract)

    def map_config_changes_to_contract(
        self, config_changes: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return map_ema_config_changes_to_contract(config_changes)
