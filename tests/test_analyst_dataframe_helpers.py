from __future__ import annotations

import pandas as pd

from analyst_dataframe_helpers import bucket_trade_performance, profit_factor, safe_merge_asof


def test_bucket_trade_performance_computes_pf_and_filters_small_buckets():
    trades = pd.DataFrame(
        {
            "duration_min": [1, 2, 3, 10, 11, 12],
            "pnl_pct": [0.02, -0.01, 0.03, 0.01, 0.02, -0.01],
        }
    )

    summary = bucket_trade_performance(
        trades,
        value_col="pnl_pct",
        bucket_col="duration_min",
        bins=[0, 5, 15],
        labels=["short", "long"],
        min_count=2,
    )

    assert list(summary["bucket"]) == ["short", "long"]
    assert summary.loc[summary["bucket"] == "short", "profit_factor"].iloc[0] == 5.0
    assert summary.loc[summary["bucket"] == "long", "profit_factor"].iloc[0] == 3.0


def test_profit_factor_returns_none_without_losses():
    assert profit_factor(pd.Series([0.01, 0.02])) is None


def test_safe_merge_asof_sorts_before_merging():
    left = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-02", "2026-01-01"]), "x": [2, 1]})
    right = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-01", "2026-01-02"]), "y": [10, 20]})

    merged = safe_merge_asof(left, right, on="timestamp")

    assert list(merged["timestamp"]) == list(pd.to_datetime(["2026-01-01", "2026-01-02"]))
    assert list(merged["y"]) == [10, 20]
