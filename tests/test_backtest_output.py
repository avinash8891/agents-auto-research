from __future__ import annotations

import json
from math import inf
from pathlib import Path

import pandas as pd
import pytest

from autoresearch_experiment import parse_benchmark_details, parse_metric
from backtest.event_diagnostics import build_event_diagnostics
from backtest.output import write_all


class _FakeEventLogger:
    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2024-01-02 09:35"),
                    "symbol": "SPY",
                    "direction": "long",
                    "event_type": "accepted_signal",
                    "reason": "",
                    "entry_price": 100.0,
                    "stop_price": 99.0,
                    "stop_distance_pct": 1.0,
                    "trigger_bar_timestamp": pd.Timestamp("2024-01-02 09:35"),
                    "entry_bar_index_from_open": 1,
                }
            ]
        )

    def write_parquet(self, path: str) -> None:
        Path(path).write_text("events")

    def event_counts_snapshot(self) -> dict[str, int]:
        return {}

    def rejection_breakdown_snapshot(self) -> dict[str, int]:
        return {}


class _InvalidEventLogger:
    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": "SPY",
                    "direction": "long",
                    "event_type": "accepted_signal",
                    "reason": "",
                }
            ]
        )

    def event_counts_snapshot(self) -> dict[str, int]:
        return {}

    def rejection_breakdown_snapshot(self) -> dict[str, int]:
        return {}


class _EmptyEventLogger:
    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame()

    def event_counts_snapshot(self) -> dict[str, int]:
        return {}

    def rejection_breakdown_snapshot(self) -> dict[str, int]:
        return {}


class _EmptyParquetEventLogger(_EmptyEventLogger):
    def write_parquet(self, path: str) -> None:
        Path(path).write_text("events")


def test_write_all_serializes_infinite_profit_factor_as_strict_json(
    tmp_path: Path,
) -> None:
    result = {
        "median_expectancy": 1.0,
        "trade_count": 1,
        "profit_factor": inf,
        "max_drawdown": 0.0,
        "diagnostics": {},
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
    assert '"profit_factor": "Infinity"' not in text
    metrics_payload = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics_payload["profit_factor"] == "Infinity"
    assert metrics_payload["median_expectancy"] == 1.0
    assert metrics_payload["diagnostics"] == {}
    assert json.loads(text)["metrics_file"] == str(tmp_path / "metrics.json")
    assert "metrics" not in json.loads(text)
    assert "strategy_diagnostics" not in json.loads(text)
    assert payload["metrics_file"] == str(tmp_path / "metrics.json")
    assert parse_metric(f"RESULT_JSON {tmp_path / 'result.json'}\n", name="profit_factor") == inf
    assert set(result) == original_keys
    assert "_trades_df" in result
    assert "_event_logger" in result
    assert json.loads((tmp_path / "diagnostics.json").read_text()) == build_event_diagnostics(
        trade_count=1,
        event_frame=_FakeEventLogger().to_dataframe(),
        event_counts={},
        rejection_breakdown={},
    )
    assert "strategy_diagnostics" not in json.loads(text)


def test_write_all_allows_missing_diagnostics_file_and_parser_accepts_it(
    tmp_path: Path,
) -> None:
    result = {
        "median_expectancy": 1.0,
        "trade_count": 0,
        "profit_factor": 1.0,
        "max_drawdown": 0.0,
        "diagnostics": {},
        "_trades_df": pd.DataFrame(),
    }

    write_all(
        result,
        {},
        tmp_path,
        strategy="ema",
        config_path="configs/ema_base.yaml",
    )

    payload = json.loads((tmp_path / "result.json").read_text())
    assert "diagnostics_file" not in payload

    details = parse_benchmark_details(f"RESULT_JSON {tmp_path / 'result.json'}\n")
    assert details["strategy_diagnostics"] == {}
    assert details["trade_count"] == 0


def test_write_all_rejects_event_frames_missing_core_schema_columns(tmp_path: Path) -> None:
    result = {
        "median_expectancy": 0.0,
        "trade_count": 0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "_trades_df": pd.DataFrame(),
        "_event_logger": _InvalidEventLogger(),
    }

    with pytest.raises(ValueError, match="STRATEGY_EVENT_SCHEMA_INVALID"):
        write_all(
            result,
            {},
            tmp_path,
            strategy="ema",
            config_path="configs/ema_base.yaml",
        )


def test_write_all_omits_strategy_events_path_for_empty_event_frames(tmp_path: Path) -> None:
    result = {
        "median_expectancy": 0.0,
        "trade_count": 0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "_trades_df": pd.DataFrame(),
        "_event_logger": _EmptyEventLogger(),
    }

    payload = write_all(
        result,
        {},
        tmp_path,
        strategy="ema",
        config_path="configs/ema_base.yaml",
    )

    assert payload["strategy_events_file"] == ""
    assert json.loads((tmp_path / "result.json").read_text())["strategy_events_file"] == ""


def test_write_all_omits_missing_strategy_events_path_for_empty_parquet_logger(
    tmp_path: Path,
) -> None:
    result = {
        "median_expectancy": 0.0,
        "trade_count": 0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "_trades_df": pd.DataFrame(),
        "_event_logger": _EmptyParquetEventLogger(),
    }

    payload = write_all(
        result,
        {},
        tmp_path,
        strategy="ema",
        config_path="configs/ema_base.yaml",
    )

    assert payload["strategy_events_file"] == ""
    assert not (tmp_path / "strategy_events.parquet").exists()


def test_runner_output_dir_default_is_not_tmp(monkeypatch) -> None:
    """F13: --output-dir must not default to /tmp."""
    monkeypatch.delenv("AUTORESEARCH_OUTPUT_DIR", raising=False)
    from backtest.runner import build_parser

    args = build_parser().parse_args(["--strategy", "ema", "--config", "x.json"])
    assert args.output_dir == "."


def test_runner_output_dir_honors_env(monkeypatch) -> None:
    """F13: --output-dir respects AUTORESEARCH_OUTPUT_DIR env var."""
    monkeypatch.setenv("AUTORESEARCH_OUTPUT_DIR", "/opt/results")
    from backtest.runner import build_parser

    args = build_parser().parse_args(["--strategy", "ema", "--config", "x.json"])
    assert args.output_dir == "/opt/results"
