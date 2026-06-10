from __future__ import annotations

import pandas as pd

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


def test_orb_backtest_applies_legacy_default_validation_window(monkeypatch) -> None:
    observed: dict[str, str] = {}
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

    run_backtest({"data_universe": "nasdaq143"})

    assert observed["validation_start"] == "2020-01-01"
    assert observed["validation_end"] == "2023-12-31"


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
