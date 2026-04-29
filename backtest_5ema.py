#!/usr/bin/env python3
"""5 EMA Backtest Runner u2014 grounded in the transcript.

Key design decisions (all from transcript):
  u2022 Short trades: generated and simulated on 5min bars only.
  u2022 Long trades: generated and simulated on 15min bars only.
  u2022 Entry at the break level (alert candle extreme), not next-bar open.
  u2022 Stop at the alert candle's opposite extreme.
  u2022 Target = entry + rr_ratio u00d7 risk.
  u2022 Each timeframe is self-contained u2014 no union-index merging.
  u2022 Inverted-risk trades (stop on wrong side of entry) are skipped.
  u2022 Positional hold allowed ("we hold it", "take it in positional").
  u2022 Slippage applied (0.05% default) on entry and exit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from data_loader import load_data
from ema_contract import compile_ema_contract
from ema_exits import simulate_trades
from metrics import compute_metrics, empty_metrics
from strategy_event_logger import StrategyEventLogger

def load_runtime_config(path: str) -> dict:
    p = Path(path)
    if p.suffix in (".yaml", ".yml"):
        import yaml
        payload = yaml.safe_load(p.read_text())
    else:
        payload = json.loads(p.read_text())
    if isinstance(payload, dict) and "runtime_config" in payload:
        config = payload["runtime_config"]
    elif isinstance(payload, dict):
        config = payload
    else:
        compilation = compile_ema_contract(payload)
        if compilation.status != "ready_to_run":
            raise ValueError(
                f"EMA contract is not runnable: status={compilation.status} "
                f"missing={compilation.missing_primitives}"
            )
        config = compilation.runtime_config
    return validate_runtime_config_scope(config, source_path=p)


def validate_runtime_config_scope(config: dict, *, source_path: Path | None = None) -> dict:
    if config.get("allow_unbounded_research_backtest"):
        return config
    missing = [
        key for key in ("validation_start", "validation_end")
        if not config.get(key)
    ]
    if missing:
        source = f" for {source_path}" if source_path is not None else ""
        raise ValueError(
            "Refusing unbounded EMA backtest"
            f"{source}: missing {', '.join(missing)}. "
            "Set validation_start and validation_end, or explicitly set "
            "allow_unbounded_research_backtest=true."
        )
    return config


# ---------------------------------------------------------------------------
# OHLC resampling
# ---------------------------------------------------------------------------

def build_timeframe_frame(
    open_: pd.Series,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    timeframe_minutes: int,
) -> pd.DataFrame:
    """Resample 5min bars to the requested timeframe."""
    rule = f"{timeframe_minutes}min"
    frame = pd.DataFrame(
        {
            "open": open_.resample(rule).first(),
            "close": close.resample(rule).last(),
            "high": high.resample(rule).max(),
            "low": low.resample(rule).min(),
        }
    ).dropna()
    return frame


# ---------------------------------------------------------------------------
# Trade simulation u2014 runs on ONE timeframe, direction-specific
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Full backtest orchestrator
# ---------------------------------------------------------------------------

def _compute_gap_up_days(open_: pd.Series, close: pd.Series, gap_pct: float) -> set:
    """Return set of dates where the stock gapped up >= gap_pct from previous close.

    Transcript (v1 lines 360-363): F&O stock + big gap up = priority for 5 EMA shorts.
    He scans pre-market for stocks gapping up 1-2%+.
    """
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
        return signals
    dates = frame.index.date
    mask = np.array([d in allowed_days for d in dates])
    signals.entries[~mask] = False
    signals.entry_price[~mask] = np.nan


def _exclude_signals_on_days(signals, frame: pd.DataFrame, excluded_days: set):
    """Zero out signals on days IN excluded_days (opposite of _filter_signals_to_days)."""
    if not excluded_days:
        return signals
    dates = frame.index.date
    mask = np.array([d in excluded_days for d in dates])
    signals.entries[mask] = False
    signals.entry_price[mask] = np.nan
    signals.stop_price[mask] = np.nan
    return signals
    signals.stop_price[~mask] = np.nan
    signals.alert_bar_idx[~mask] = -1
    return signals


def _log_filter_rejections(
    event_logger: StrategyEventLogger,
    frame: pd.DataFrame,
    before_mask: np.ndarray,
    after_mask: np.ndarray,
    entry_prices: np.ndarray,
    stop_prices: np.ndarray,
    symbol: str,
    direction: str,
    reason: str,
    filter_name: str | None = None,
    filter_value: str | None = None,
    filter_threshold: str | None = None,
    entry_cutoff_time: str | None = None,
) -> None:
    """Record signals killed by a filter (vectorized)."""
    killed = before_mask & ~after_mask
    event_logger.record_events(
        timestamps=frame.index,
        mask=killed,
        symbol=symbol,
        direction=direction,
        event_type="rejected_signal",
        reason=reason,
        entry_prices=entry_prices,
        stop_prices=stop_prices,
    )


def _log_raw_setups(
    event_logger: StrategyEventLogger,
    frame: pd.DataFrame,
    signals,
    symbol: str,
    direction: str,
) -> None:
    """Record raw setups (vectorized)."""
    event_logger.record_events(
        timestamps=frame.index,
        mask=signals.entries.values,
        symbol=symbol,
        direction=direction,
        event_type="raw_setup",
        entry_prices=signals.entry_price.values,
        stop_prices=signals.stop_price.values,
    )


def _log_accepted_signals(
    event_logger: StrategyEventLogger,
    frame: pd.DataFrame,
    signals,
    symbol: str,
    direction: str,
) -> None:
    """Record accepted signals (vectorized)."""
    event_logger.record_events(
        timestamps=frame.index,
        mask=signals.entries.values,
        symbol=symbol,
        direction=direction,
        event_type="accepted_signal",
        entry_prices=signals.entry_price.values,
        stop_prices=signals.stop_price.values,
    )


def run_backtest(config: dict) -> dict:
    from ema_signals import generate_signals_for_frame

    event_logger = StrategyEventLogger()

    batch = load_data(
        config.get("data_dir", "data"),
        symbols=config.get("symbols"),
        start_date=config.get("validation_start"),
        end_date=config.get("validation_end"),
    )
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

    # Entry cutoff: only allow entries before this time (e.g. "10:30")
    entry_cutoff_time = config.get("entry_cutoff_time", None)

    # Gap filter: only trade on days with significant gaps
    gap_filter = config.get("gap_filter", False)
    gap_pct = config.get("gap_pct", 0.01)  # 1% default

    # Gap exclude: skip shorts on gap-up days (fading inflated opens is risky)
    gap_exclude = config.get("gap_exclude", False)
    gap_exclude_pct = config.get("gap_exclude_pct", 0.005)  # 0.5% default

    # Minimum stop distance: filter out trades where the stop is too tight for
    # slippage to be absorbed.  Value is in percent (e.g. 0.20 = 0.20%).
    min_stop_distance_pct = config.get("min_stop_distance_pct", None)
    if min_stop_distance_pct is not None:
        min_stop_distance_pct = float(min_stop_distance_pct)

    # Maximum stop distance: filter out trades where the alert candle is too large
    # (extreme moves likely to reverse).  Value is in percent (e.g. 0.50 = 0.50%).
    max_stop_distance_pct = config.get("max_stop_distance_pct", None)
    if max_stop_distance_pct is not None:
        max_stop_distance_pct = float(max_stop_distance_pct)

    # Daily trade cap: transcript says 3-5 trades per day TOTAL across all symbols.
    # T2 (Best Use): "When I had completed five trades, I would end them"
    # T4 (Game Changer): "not more than 3 times a day"
    max_trades_per_day = config.get("max_trades_per_day", None)

    all_trades: list[dict] = []

    for symbol in close.columns:
        sym_open = open_[symbol].dropna()
        sym_close = close[symbol].dropna()
        sym_high = high[symbol].dropna()
        sym_low = low[symbol].dropna()

        if sym_close.empty:
            continue

        # Pre-compute gap days for this symbol
        gap_up_days = _compute_gap_up_days(sym_open, sym_close, gap_pct) if gap_filter else None
        gap_down_days = _compute_gap_down_days(sym_open, sym_close, gap_pct) if gap_filter else None
        # Gap-exclude days: gap-up days where shorts should be skipped
        gap_exclude_up_days = _compute_gap_up_days(sym_open, sym_close, gap_exclude_pct) if gap_exclude else None

        # LONG signals on 15min chart, simulated on 15min bars
        if direction_bias in {"both", "long_only"}:
            long_frame = build_timeframe_frame(sym_open, sym_close, sym_high, sym_low, tf_long)
            long_signals = generate_signals_for_frame(
                long_frame, "long", ema_length,
                use_range_shift=use_range_shift,
                range_shift_lookback=range_shift_lookback,
            )

            _log_raw_setups(event_logger, long_frame, long_signals, symbol, "long")

            if entry_cutoff_time and isinstance(long_frame.index, pd.DatetimeIndex):
                before = long_signals.entries.values.copy()
                cutoff = pd.Timestamp(entry_cutoff_time).time()
                mask = long_frame.index.time <= cutoff
                long_signals.entries[~mask] = False
                long_signals.entry_price[~mask] = np.nan
                long_signals.stop_price[~mask] = np.nan
                _log_filter_rejections(
                    event_logger, long_frame, before, long_signals.entries.values,
                    long_signals.entry_price.values, long_signals.stop_price.values,
                    symbol, "long", "entry_cutoff",
                    filter_name="entry_cutoff_time",
                    filter_threshold=entry_cutoff_time,
                    entry_cutoff_time=entry_cutoff_time,
                )
            if gap_filter:
                before = long_signals.entries.values.copy()
                _filter_signals_to_days(long_signals, long_frame, gap_down_days)
                _log_filter_rejections(
                    event_logger, long_frame, before, long_signals.entries.values,
                    long_signals.entry_price.values, long_signals.stop_price.values,
                    symbol, "long", "gap_filter",
                    filter_name="gap_filter", filter_threshold=str(gap_pct),
                )

            if min_stop_distance_pct is not None:
                before = long_signals.entries.values.copy()
                stop_dist = np.abs(long_signals.entry_price - long_signals.stop_price) / long_signals.entry_price * 100
                too_tight = stop_dist < min_stop_distance_pct
                long_signals.entries[too_tight] = False
                long_signals.entry_price[too_tight] = np.nan
                long_signals.stop_price[too_tight] = np.nan
                _log_filter_rejections(
                    event_logger, long_frame, before, long_signals.entries.values,
                    long_signals.entry_price.values, long_signals.stop_price.values,
                    symbol, "long", "min_stop_distance",
                    filter_name="min_stop_distance_pct",
                    filter_threshold=str(min_stop_distance_pct),
                )

            if max_stop_distance_pct is not None:
                before = long_signals.entries.values.copy()
                stop_dist = np.abs(long_signals.entry_price - long_signals.stop_price) / long_signals.entry_price * 100
                too_wide = stop_dist > max_stop_distance_pct
                long_signals.entries[too_wide] = False
                long_signals.entry_price[too_wide] = np.nan
                long_signals.stop_price[too_wide] = np.nan
                _log_filter_rejections(
                    event_logger, long_frame, before, long_signals.entries.values,
                    long_signals.entry_price.values, long_signals.stop_price.values,
                    symbol, "long", "max_stop_distance",
                    filter_name="max_stop_distance_pct",
                    filter_threshold=str(max_stop_distance_pct),
                )

            _log_accepted_signals(event_logger, long_frame, long_signals, symbol, "long")

            for t in simulate_trades(long_frame, long_signals, config,
                                     symbol=symbol, event_logger=event_logger):
                t["symbol"] = symbol
                all_trades.append(t)

        # SHORT signals on 5min chart, simulated on 5min bars
        if direction_bias in {"both", "short_only"}:
            short_frame = build_timeframe_frame(sym_open, sym_close, sym_high, sym_low, tf_short)
            short_signals = generate_signals_for_frame(
                short_frame, "short", ema_length,
                use_range_shift=use_range_shift,
                range_shift_lookback=range_shift_lookback,
            )

            _log_raw_setups(event_logger, short_frame, short_signals, symbol, "short")

            if entry_cutoff_time and isinstance(short_frame.index, pd.DatetimeIndex):
                before = short_signals.entries.values.copy()
                cutoff = pd.Timestamp(entry_cutoff_time).time()
                mask = short_frame.index.time <= cutoff
                short_signals.entries[~mask] = False
                short_signals.entry_price[~mask] = np.nan
                short_signals.stop_price[~mask] = np.nan
                _log_filter_rejections(
                    event_logger, short_frame, before, short_signals.entries.values,
                    short_signals.entry_price.values, short_signals.stop_price.values,
                    symbol, "short", "entry_cutoff",
                    filter_name="entry_cutoff_time",
                    filter_threshold=entry_cutoff_time,
                    entry_cutoff_time=entry_cutoff_time,
                )
            if gap_filter:
                # Short on gap-up days (fade the gap — transcript core rule)
                before = short_signals.entries.values.copy()
                _filter_signals_to_days(short_signals, short_frame, gap_up_days)
                _log_filter_rejections(
                    event_logger, short_frame, before, short_signals.entries.values,
                    short_signals.entry_price.values, short_signals.stop_price.values,
                    symbol, "short", "gap_filter",
                    filter_name="gap_filter", filter_threshold=str(gap_pct),
                )

            if gap_exclude and gap_exclude_up_days:
                before = short_signals.entries.values.copy()
                _exclude_signals_on_days(short_signals, short_frame, gap_exclude_up_days)
                _log_filter_rejections(
                    event_logger, short_frame, before, short_signals.entries.values,
                    short_signals.entry_price.values, short_signals.stop_price.values,
                    symbol, "short", "gap_exclude",
                    filter_name="gap_exclude", filter_threshold=str(gap_exclude_pct),
                )

            if min_stop_distance_pct is not None:
                before = short_signals.entries.values.copy()
                stop_dist = np.abs(short_signals.entry_price - short_signals.stop_price) / short_signals.entry_price * 100
                too_tight = stop_dist < min_stop_distance_pct
                short_signals.entries[too_tight] = False
                short_signals.entry_price[too_tight] = np.nan
                short_signals.stop_price[too_tight] = np.nan
                _log_filter_rejections(
                    event_logger, short_frame, before, short_signals.entries.values,
                    short_signals.entry_price.values, short_signals.stop_price.values,
                    symbol, "short", "min_stop_distance",
                    filter_name="min_stop_distance_pct",
                    filter_threshold=str(min_stop_distance_pct),
                )

            if max_stop_distance_pct is not None:
                before = short_signals.entries.values.copy()
                stop_dist = np.abs(short_signals.entry_price - short_signals.stop_price) / short_signals.entry_price * 100
                too_wide = stop_dist > max_stop_distance_pct
                short_signals.entries[too_wide] = False
                short_signals.entry_price[too_wide] = np.nan
                short_signals.stop_price[too_wide] = np.nan
                _log_filter_rejections(
                    event_logger, short_frame, before, short_signals.entries.values,
                    short_signals.entry_price.values, short_signals.stop_price.values,
                    symbol, "short", "max_stop_distance",
                    filter_name="max_stop_distance_pct",
                    filter_threshold=str(max_stop_distance_pct),
                )

            _log_accepted_signals(event_logger, short_frame, short_signals, symbol, "short")

            for t in simulate_trades(short_frame, short_signals, config,
                                     symbol=symbol, event_logger=event_logger):
                t["symbol"] = symbol
                all_trades.append(t)

    if not all_trades:
        result = empty_metrics()
        result["_event_logger"] = event_logger
        return result

    trades_df = pd.DataFrame(all_trades)

    # Apply daily trade cap: keep first N trades per day across all symbols
    if max_trades_per_day and max_trades_per_day > 0:
        trades_df = trades_df.sort_values("entry_date")
        trades_df["_date"] = trades_df["entry_date"].dt.date
        kept = trades_df.groupby("_date").head(max_trades_per_day)
        rejected_idx = trades_df.index.difference(kept.index)
        if len(rejected_idx):
            rej = trades_df.loc[rejected_idx, ["entry_date", "symbol", "direction", "entry_price", "stop"]].copy()
            rej = rej.rename(columns={"entry_date": "timestamp", "stop": "stop_price"})
            rej["event_type"] = "rejected_signal"
            rej["reason"] = "max_trades_per_day"
            event_logger.record_dataframe(rej, "rejected_signal", "max_trades_per_day")
        trades_df = kept.drop(columns=["_date"])

    result = compute_metrics(trades_df)
    result["_trades_df"] = trades_df
    result["_event_logger"] = event_logger
    return result




# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _git_sha() -> str:
    """Current git SHA, or 'unknown' if not in a repo."""
    import subprocess as _sp
    try:
        return _sp.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=_sp.DEVNULL, text=True,
        ).strip()
    except Exception:
        return "unknown"


def _config_hash(config: dict) -> str:
    """Deterministic hash of the runtime config."""
    import hashlib
    blob = json.dumps(config, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def main() -> None:
    parser = argparse.ArgumentParser(description="5 EMA Backtest Runner")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", default="/tmp",
                        help="Directory to write result.json and trades CSV")
    args = parser.parse_args()
    config = load_runtime_config(args.config)

    # --- Run on validation (primary) split ---
    result = run_backtest(config)
    trades_df = result.pop("_trades_df", None)
    event_logger = result.pop("_event_logger", None)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write trades CSV
    trades_path = ""
    if trades_df is not None and not trades_df.empty:
        trades_path = str(output_dir / "trades.csv")
        trades_df.to_csv(trades_path, index=False)

    # Write strategy events as parquet (vectorized, compact, analyst-ready)
    events_path = ""
    if event_logger:
        events_path = str(output_dir / "strategy_events.parquet")
        event_logger.write_parquet(events_path)

    # Write diagnostics.json
    diagnostics_path = ""
    trade_count = len(trades_df) if trades_df is not None else 0
    strategy_diagnostics = {}
    if event_logger:
        diagnostics_path = str(output_dir / "diagnostics.json")
        strategy_diagnostics = event_logger.write_diagnostics(diagnostics_path, trade_count)

    # Write structured result.json — single source of truth for the loop
    from datetime import datetime, timezone
    result_payload = {
        "family": "ema",
        "config": args.config,
        "config_hash": _config_hash(config),
        "git_sha": _git_sha(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "median_expectancy": result["median_expectancy"],
            "trade_count": result["trade_count"],
            "profit_factor": result["profit_factor"],
            "max_drawdown": result["max_drawdown"],
            "pct_profitable_windows": result["pct_profitable_windows"],
            "avg_sharpe_across_windows": result["avg_sharpe_across_windows"],
        },
        "diagnostics": result.get("diagnostics", {}),
        "strategy_diagnostics": strategy_diagnostics,
        "trades_file": trades_path,
        "strategy_events_file": events_path,
        "diagnostics_file": diagnostics_path,
    }
    result_json_path = output_dir / "result.json"
    result_json_path.write_text(json.dumps(result_payload, indent=2) + "\n")

    # Single structured output line — loop reads the file, not stdout
    print(f"RESULT_JSON {result_json_path}")
    if events_path:
        print(f"STRATEGY_EVENTS_FILE {events_path}")
    if diagnostics_path:
        print(f"DIAGNOSTICS_FILE {diagnostics_path}")


if __name__ == "__main__":
    main()
