from __future__ import annotations

import math
from pathlib import Path

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

    assert "pct_profitable" + "_windows" not in metrics
    assert "avg_sharpe" + "_across_windows" not in metrics


def test_fabricated_window_metric_names_are_absent_from_runtime_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "pct_profitable" + "_windows",
        "avg_sharpe" + "_across_windows",
    )
    runtime_sources = (
        root / "metrics.py",
        root / "thesis_validator.py",
        root / "autoresearch_experiment.py",
        root / "research_prompts.py",
    )

    remaining: list[str] = []
    for path in runtime_sources:
        text = path.read_text()
        for metric_name in forbidden:
            if metric_name in text:
                remaining.append(f"{path.relative_to(root)}:{metric_name}")

    assert remaining == []
