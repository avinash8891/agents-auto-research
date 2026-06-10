from __future__ import annotations

import math

import pandas as pd

from metrics import _profit_factor_from_pnl, compute_metrics


def test_profit_factor_is_infinite_when_there_are_only_winners() -> None:
    assert math.isinf(_profit_factor_from_pnl([1.0, 2.0, 3.0]))


def test_profit_factor_is_zero_when_there_are_no_winners_and_no_losses() -> None:
    assert _profit_factor_from_pnl([0.0, 0.0]) == 0.0


def test_compute_metrics_omits_window_metrics_until_walkforward_computes_them() -> None:
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

    assert "pct_profitable_windows" not in metrics
    assert "avg_sharpe_across_windows" not in metrics
