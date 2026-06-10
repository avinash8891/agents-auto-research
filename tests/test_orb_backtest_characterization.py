from __future__ import annotations

import pandas as pd
import pytest

from strategies.orb.runner import run_backtest
from strategies.orb.signals import _cap_first_entry_per_day, generate_orb_signals


def test_orb_backtest_returns_empty_metrics_for_empty_validation_window(monkeypatch) -> None:
    empty = pd.DataFrame()

    monkeypatch.setattr(
        "strategies.orb.runner.load_universe_data",
        lambda config: {
            "open": empty,
            "high": empty,
            "low": empty,
            "close": empty,
            "volume": None,
        },
    )

    result = run_backtest(
        {
            "validation_start": "2024-01-01",
            "validation_end": "2024-01-02",
            "data_universe": "nasdaq143",
        }
    )

    assert result["trade_count"] == 0
    assert result["_trades_df"].empty
    assert result["_event_logger"] is None


def test_orb_backtest_rejects_unbounded_window_without_explicit_flag(monkeypatch) -> None:
    empty = pd.DataFrame()

    def _capture(config: dict) -> dict[str, pd.DataFrame | None]:
        return {
            "open": empty,
            "high": empty,
            "low": empty,
            "close": empty,
            "volume": None,
        }

    monkeypatch.setattr("strategies.orb.runner.load_universe_data", _capture)

    with pytest.raises(ValueError, match="Refusing unbounded ORB backtest"):
        run_backtest({"data_universe": "nasdaq143"})


def test_orb_backtest_does_not_default_dates_for_unbounded_research(monkeypatch) -> None:
    observed: dict[str, str | None] = {}
    empty = pd.DataFrame()

    def _capture(config: dict) -> dict[str, pd.DataFrame | None]:
        observed["validation_start"] = config.get("validation_start")
        observed["validation_end"] = config.get("validation_end")
        return {
            "open": empty,
            "high": empty,
            "low": empty,
            "close": empty,
            "volume": None,
        }

    monkeypatch.setattr("strategies.orb.runner.load_universe_data", _capture)

    run_backtest({"data_universe": "nasdaq143", "allow_unbounded_research_backtest": True})

    assert observed["validation_start"] is None
    assert observed["validation_end"] is None


def test_orb_backtest_computes_metrics_in_chronological_trade_order(monkeypatch) -> None:
    idx = pd.to_datetime(
        [
            "2024-01-02 09:30",
            "2024-01-02 09:35",
            "2024-01-02 09:40",
            "2024-01-03 09:30",
            "2024-01-03 09:35",
            "2024-01-03 09:40",
            "2024-01-04 09:30",
            "2024-01-04 09:35",
            "2024-01-04 09:40",
        ]
    )
    columns = ["AAA", "BBB"]
    open_ = pd.DataFrame(100.0, index=idx, columns=columns)
    high = pd.DataFrame(100.5, index=idx, columns=columns)
    low = pd.DataFrame(99.5, index=idx, columns=columns)
    close = pd.DataFrame(100.0, index=idx, columns=columns)
    volume = pd.DataFrame(1000, index=idx, columns=columns)

    for timestamp in idx[::3]:
        open_.loc[timestamp] = 100.5
        high.loc[timestamp] = 101.0
        low.loc[timestamp] = 100.0
        close.loc[timestamp] = 100.5

    # Day 1: BBB short loses.
    close.loc[pd.Timestamp("2024-01-02 09:35"), "BBB"] = 99.0
    open_.loc[pd.Timestamp("2024-01-02 09:40"), "BBB"] = 100.0
    high.loc[pd.Timestamp("2024-01-02 09:40"), "BBB"] = 102.0
    low.loc[pd.Timestamp("2024-01-02 09:40"), "BBB"] = 99.0

    # Day 2: AAA long wins. apply_exits emits this before the shorts because
    # it concatenates all long trades before all short trades.
    close.loc[pd.Timestamp("2024-01-03 09:35"), "AAA"] = 102.0
    open_.loc[pd.Timestamp("2024-01-03 09:40"), "AAA"] = 101.0
    high.loc[pd.Timestamp("2024-01-03 09:40"), "AAA"] = 103.0
    low.loc[pd.Timestamp("2024-01-03 09:40"), "AAA"] = 100.5
    close.loc[pd.Timestamp("2024-01-03 09:40"), "AAA"] = 102.0

    # Day 3: BBB short loses again.
    close.loc[pd.Timestamp("2024-01-04 09:35"), "BBB"] = 99.0
    open_.loc[pd.Timestamp("2024-01-04 09:40"), "BBB"] = 100.0
    high.loc[pd.Timestamp("2024-01-04 09:40"), "BBB"] = 102.0
    low.loc[pd.Timestamp("2024-01-04 09:40"), "BBB"] = 99.0

    monkeypatch.setattr(
        "strategies.orb.runner.load_universe_data",
        lambda config: {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
    )

    result = run_backtest(
        {
            "data_universe": "fixture",
            "or_minutes": 5,
            "timeframe_minutes": 5,
            "rr_ratio": 1.0,
            "slippage_pct": 0.0,
            "max_hold_bars": 1,
            "max_one_entry_per_day": False,
        }
    )

    assert result["_trades_df"]["entry_date"].is_monotonic_increasing
    assert result["_trades_df"]["direction"].tolist() == ["short", "long", "short"]
    assert result["max_drawdown"] == 0.01


def test_orb_max_one_entry_per_day_limits_both_directions() -> None:
    idx = pd.to_datetime(
        [
            "2024-01-02 09:30",
            "2024-01-02 09:35",
            "2024-01-02 09:40",
            "2024-01-02 09:45",
        ]
    )
    frame = pd.DataFrame(
        {
            "open": [9.5, 10.0, 10.2, 9.0],
            "high": [10.0, 10.6, 10.3, 9.3],
            "low": [9.0, 10.4, 8.4, 8.9],
            "close": [9.5, 10.5, 8.5, 9.1],
            "volume": [100, 100, 100, 100],
        },
        index=idx,
    )

    signals = generate_orb_signals(
        frame[["open"]],
        frame[["high"]],
        frame[["low"]],
        frame[["close"]],
        volume=frame[["volume"]],
        config={
            "or_minutes": 5,
            "timeframe_minutes": 5,
            "max_one_entry_per_day": True,
        },
    )

    assert signals.entries_long.sum().item() == 1
    assert signals.entries_short.sum().item() == 0


def test_orb_first_entry_cap_prefers_long_on_same_bar_tie() -> None:
    idx = pd.to_datetime(
        [
            "2024-01-02 09:30",
            "2024-01-02 09:35",
            "2024-01-02 09:40",
        ]
    )
    day_ids = pd.Index(idx.date).factorize()[0]
    broke_above_df = pd.DataFrame(
        [[False], [True], [False]],
        index=idx,
        columns=["AAA"],
    )
    broke_below_df = pd.DataFrame(
        [[False], [True], [True]],
        index=idx,
        columns=["AAA"],
    )

    capped_long, capped_short = _cap_first_entry_per_day(broke_above_df, broke_below_df, day_ids)

    assert capped_long.sum().item() == 1
    assert capped_short.sum().item() == 0
