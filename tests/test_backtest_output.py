from __future__ import annotations

import json
from math import inf
from pathlib import Path

from autoresearch_experiment import parse_metric
from backtest.output import write_all


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
    }

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
