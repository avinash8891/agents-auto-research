from __future__ import annotations

import json
from math import inf
from pathlib import Path

import pandas as pd

from autoresearch_experiment import parse_metric
from backtest.output import write_all


class _FakeEventLogger:
    def write_parquet(self, path: str) -> None:
        Path(path).write_text("events")

    def write_diagnostics(self, path: str, trade_count: int) -> dict[str, int]:
        Path(path).write_text(json.dumps({"trade_count": trade_count}))
        return {"trade_count": trade_count}


def test_write_all_serializes_infinite_profit_factor_as_strict_json(
    tmp_path: Path,
) -> None:
    result = {
        "median_expectancy": 1.0,
        "trade_count": 1,
        "profit_factor": inf,
        "max_drawdown": 0.0,
        "pct_profitable_windows": 1.0,
        "avg_sharpe_across_windows": 0.0,
        "_trades_df": pd.DataFrame([{"trade_id": 1}]),
        "_event_logger": _FakeEventLogger(),
    }
    original_keys = set(result)

    payload = write_all(
        result,
        {},
        tmp_path,
        strategy="ema",
        config_path="configs/ema_base.yaml",
    )

    text = (tmp_path / "result.json").read_text()
    assert '"profit_factor": "Infinity"' in text
    assert json.loads(text)["metrics"]["profit_factor"] == "Infinity"
    assert payload["metrics"]["profit_factor"] == inf
    assert parse_metric(f"RESULT_JSON {tmp_path / 'result.json'}\n", name="profit_factor") == inf
    assert set(result) == original_keys
    assert "_trades_df" in result
    assert "_event_logger" in result
