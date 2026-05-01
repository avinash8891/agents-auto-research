from __future__ import annotations

import pandas as pd

from strategies.orb.runner import run_backtest


def test_orb_backtest_returns_empty_metrics_for_empty_validation_window(monkeypatch) -> None:
    empty = pd.DataFrame()

    monkeypatch.setattr(
        "strategies.orb.runner.load_data",
        lambda *args, **kwargs: {
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
            "data_dir": "data",
        }
    )

    assert result["trade_count"] == 0
    assert result["_trades_df"].empty
    assert result["_event_logger"] is None
