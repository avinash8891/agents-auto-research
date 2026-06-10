from __future__ import annotations

import math

import pandas as pd

from metrics import _profit_factor_from_pnl, compute_metrics


def test_profit_factor_is_infinite_when_there_are_only_winners() -> None:
    assert math.isinf(_profit_factor_from_pnl([1.0, 2.0, 3.0]))


def test_profit_factor_is_zero_when_there_are_no_winners_and_no_losses() -> None:
    assert _profit_factor_from_pnl([0.0, 0.0]) == 0.0


def test_window_metrics_use_six_month_calendar_windows_not_trade_level_statistics() -> None:
    trades = pd.DataFrame(
        {
            "entry_date": pd.to_datetime(
                [
                    "2026-01-02",
                    "2026-02-02",
                    "2026-07-02",
                    "2026-08-02",
                ]
            ),
            "pnl_pct": [1.0, -1.0, 1.0, 1.0],
            "exit_reason": ["target", "stop", "target", "target"],
        }
    )

    metrics = compute_metrics(trades)

    assert metrics["pct_profitable_windows"] == 0.5
    assert metrics["avg_sharpe_across_windows"] == 0.0
