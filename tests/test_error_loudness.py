from __future__ import annotations

import logging
import math
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agent_infra import _parse_json as parse_agent_json
from autoresearch_research import load_baseline_config
from backtest_run_db import BaselineCheckpoint, BaselineTracker
from metrics import compute_diagnostics, compute_metrics
from research_paths import _parse_json as parse_research_json
from strategy_event_logger import StrategyEventLogger
from thesis_validator import load_prior_theses


def test_agent_json_parser_logs_malformed_json(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)

    assert parse_agent_json("{bad json}") is None

    assert "AGENT_JSON_PARSE_FAILED" in caplog.text


def test_research_json_parser_logs_malformed_json(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)

    assert parse_research_json("{bad json}") is None

    assert "RESEARCH_JSON_PARSE_FAILED" in caplog.text


def test_load_baseline_config_raises_on_malformed_yaml(tmp_path: Path) -> None:
    family = SimpleNamespace(base_config_filename="ema_base.yaml")
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: [\n: : :")

    with pytest.raises(ValueError, match="BASELINE_CONFIG_LOAD_FAILED"):
        load_baseline_config(tmp_path, family)


def test_load_prior_theses_logs_bad_sqlite_row(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from backtest_run_db import BacktestRunDB

    caplog.set_level(logging.WARNING)
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.seed_research_thesis_attempts_rows(
        [
            {"not": "a valid thesis attempt row"},
            {
                "research_round_id": "r1",
                "attempt_number": 1,
                "thesis_id": "valid_thesis",
                "config_changes": {"ema_length": 7},
                "validator_status": "accepted",
                "strategy_family": "ema",
                "selected_for_execution": 1,
                "created_at_utc": "2026-04-30T00:00:00+00:00",
            },
        ],
    )

    prior = load_prior_theses(tmp_path, db=db)

    assert prior[-1]["thesis_id"] == "valid_thesis"
    assert "PRIOR_THESES_SQLITE_INVALID" in caplog.text


def test_load_prior_theses_uses_runtime_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backtest_run_db import BacktestRunDB

    code_root = tmp_path / "code-root"
    runtime_root = tmp_path / "runtime-root"
    code_root.mkdir()
    runtime_root.mkdir()
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(runtime_root))

    db = BacktestRunDB(runtime_root / "ema_backtest_runs.db")
    db.seed_research_thesis_attempts_rows(
        [
            {
                "research_round_id": "job-1-round-1",
                "attempt_number": 1,
                "thesis_id": "runtime_prior_thesis",
                "config_changes": {"ema_length": 13},
                "validator_status": "accepted",
                "strategy_family": "ema",
                "selected_for_execution": 1,
                "created_at_utc": "2026-04-30T00:00:00+00:00",
            },
        ],
    )

    prior = load_prior_theses(code_root)

    assert prior[-1]["thesis_id"] == "runtime_prior_thesis"


def test_load_prior_theses_skips_config_less_rows(tmp_path: Path) -> None:
    from backtest_run_db import BacktestRunDB

    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.seed_research_thesis_attempts_rows(
        [
            {
                "research_round_id": "r1",
                "attempt_number": 1,
                "thesis_id": "liquidity_decay_dimension",
                "config_changes": {},
                "validator_status": "halted",
                "strategy_family": "ema",
                "thesis_details": {},
                "selected_for_execution": 0,
                "created_at_utc": "2026-04-30T00:00:00+00:00",
            },
        ],
    )

    # Dedupe operates on config changes; rows without any are not priors.
    assert load_prior_theses(tmp_path, db=db) == []


def test_baseline_drift_flags_nonnumeric_metrics(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)
    tracker = BaselineTracker(Path("/tmp/not-used-baseline.json"))
    tracker._checkpoints = [
        BaselineCheckpoint(
            code_commit="a",
            data_hash="d",
            config_hash="c",
            metrics={"profit_factor": "bad"},
        )
    ]
    current = BaselineCheckpoint(
        code_commit="a",
        data_hash="d",
        config_hash="c",
        metrics={"profit_factor": 99.0},
    )

    drift = tracker.check_drift(current)

    assert drift["drifted"] is True
    assert drift["details"][0]["field"] == "profit_factor"
    assert drift["details"][0]["severity"] == "critical"
    assert "BASELINE_DRIFT_METRIC_INVALID" in caplog.text


def test_compute_diagnostics_reports_unbucketable_stop_distance(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    trades = pd.DataFrame(
        {
            "pnl_pct": [1, -1, 2, -2, 1, -1],
            "entry_price": [100] * 6,
            "stop": [99] * 6,
        }
    )

    diag = compute_diagnostics(trades)

    assert diag["pf_by_stop_distance_quintile"]["status"] == "unavailable"
    assert "STOP_DISTANCE_QUINTILE_UNAVAILABLE" in caplog.text


def test_compute_diagnostics_all_wins_symbol_bucket_uses_explicit_pf_sentinel() -> None:
    trades = pd.DataFrame(
        {
            "pnl_pct": [1.0, 0.5, 0.25, 0.75, 0.1],
            "symbol": ["SPY"] * 5,
        }
    )

    diag = compute_diagnostics(trades)

    assert math.isinf(diag["pf_by_symbol_best10"][0]["pf"])


def test_compute_metrics_zero_trades_returns_sentinel_values() -> None:
    trades = pd.DataFrame({"pnl_pct": []})

    metrics = compute_metrics(trades)

    assert metrics["trade_count"] == 0
    assert metrics["median_expectancy"] == 0.0
    assert metrics["profit_factor"] == 0.0
    assert "pct_profitable_windows" not in metrics
    assert "avg_sharpe_across_windows" not in metrics


def test_compute_metrics_omits_fabricated_window_metrics() -> None:
    trades = pd.DataFrame({"pnl_pct": [0.02, -0.01, 0.03]})

    metrics = compute_metrics(trades)

    assert "pct_profitable_windows" not in metrics
    assert "avg_sharpe_across_windows" not in metrics


def test_prompts_do_not_advertise_fabricated_window_metrics() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    prompt_paths = [
        repo_root / "research_prompts.py",
    ]

    for prompt_path in prompt_paths:
        text = prompt_path.read_text()
        assert "pct_profitable_windows" not in text
        assert "avg_sharpe_across_windows" not in text


def test_compute_metrics_all_wins_uses_explicit_profit_factor_sentinel() -> None:
    trades = pd.DataFrame({"pnl_pct": [1.0, 0.5, 2.0]})

    metrics = compute_metrics(trades)

    assert math.isinf(metrics["profit_factor"])


def test_strategy_event_logger_logs_invalid_timestamp(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    logger = StrategyEventLogger()

    logger.log(timestamp="not-a-date", event_type="accepted_signal", symbol="SPY")

    assert "STRATEGY_EVENT_TIMESTAMP_INVALID" in caplog.text
