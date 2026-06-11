from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backtest_run_db import BacktestRunDB
from causal_model import load_model, save_model
from experiment_evaluator import evaluate_predictions
from research_types import CausalFactor, CausalModel
from walkforward import (
    build_windows,
    evaluate_walkforward,
    run_walkforward_queue,
    walkforward_config,
)


def _seed_run(db_path: Path, *, run_id: str = "run-thesis") -> None:
    db = BacktestRunDB(db_path)
    db.add_from_sqlite_fields(
        run_id=run_id,
        thesis_id="thesis-001",
        config_path="runtime/jobs/job-1/research/round-1/selected_config.json",
        runtime_config={"ema_length": 8},
        code_commit="abcdef1",
        data_hash="data",
        metrics={"profit_factor": 1.2, "trade_count": 30},
        trade_analysis={},
        strategy_diagnostics={},
        decision_status="keep",
        verdict_status="supported",
        verdict_summary="supported",
        family="ema",
        job_id=1,
        primary_metric_name="profit_factor",
        primary_metric_value=1.2,
        research_round_id="job-1-round-1",
        research_round_number=1,
    )


def _predictions() -> list[dict]:
    return [
        {"metric": "profit_factor", "direction": "increase", "predicted": 1.5},
        {"metric": "trade_count", "direction": "not_worse_than", "predicted": 20},
    ]


def _windows():
    return build_windows("2020-01-01", "2021-04-01")


def test_build_windows_records_train_window_but_uses_test_window_geometry() -> None:
    windows = _windows()

    assert [window.test_start for window in windows] == [
        "2020-07-01T00:00:00+00:00",
        "2020-10-01T00:00:00+00:00",
        "2021-01-01T00:00:00+00:00",
    ]
    assert windows[0].train_start == "2020-01-01T00:00:00+00:00"
    assert windows[0].train_end == windows[0].test_start


def test_walkforward_robust_fixture_graduates_and_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    db_path = tmp_path / "ema_backtest_runs.db"
    _seed_run(db_path)

    report = evaluate_walkforward(
        family="ema",
        thesis_id="thesis-001",
        runtime_root=tmp_path,
        db_path=db_path,
        run_id="run-thesis",
        windows=_windows(),
        predictions=_predictions(),
        baseline_metrics=[
            {"profit_factor": 1.0, "trade_count": 30},
            {"profit_factor": 1.0, "trade_count": 30},
            {"profit_factor": 1.0, "trade_count": 30},
        ],
        candidate_metrics=[
            {"profit_factor": 1.2, "trade_count": 30},
            {"profit_factor": 1.1, "trade_count": 31},
            {"profit_factor": 0.9, "trade_count": 30},
        ],
    )

    persisted = json.loads((tmp_path / "walkforward" / "thesis-001.json").read_text())
    with sqlite3.connect(db_path) as conn:
        graduated = conn.execute(
            "SELECT graduated FROM backtest_runs WHERE run_id = 'run-thesis'"
        ).fetchone()[0]
    assert report["graduated"] is True
    assert persisted["survival_rate"] == pytest.approx(2 / 3)
    assert persisted["windows"][0]["directions_hold"] is True
    assert graduated == 1


def test_walkforward_curve_fit_fixture_demotes_factor_and_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    db_path = tmp_path / "ema_backtest_runs.db"
    _seed_run(db_path)
    save_model(
        CausalModel(
            family="ema",
            version=1,
            factors=[
                CausalFactor(
                    factor_id="f001",
                    story="curve-fit gap rule",
                    rule="gap_pct < 0",
                    direction="win",
                    status="harvested",
                )
            ],
            accuracy_history=[],
        )
    )

    report = evaluate_walkforward(
        family="ema",
        thesis_id="thesis-001",
        runtime_root=tmp_path,
        db_path=db_path,
        run_id="run-thesis",
        windows=_windows(),
        predictions=_predictions(),
        baseline_metrics=[
            {"profit_factor": 1.0, "trade_count": 30},
            {"profit_factor": 1.0, "trade_count": 30},
            {"profit_factor": 1.0, "trade_count": 30},
        ],
        candidate_metrics=[
            {"profit_factor": 1.2, "trade_count": 30},
            {"profit_factor": 0.8, "trade_count": 30},
            {"profit_factor": 0.7, "trade_count": 30},
        ],
        factor_rule="gap_pct < 0",
    )

    with sqlite3.connect(db_path) as conn:
        graduated = conn.execute(
            "SELECT graduated FROM backtest_runs WHERE run_id = 'run-thesis'"
        ).fetchone()[0]
    model = load_model("ema")
    assert report["graduated"] is False
    assert report["verdict"] == "demoted"
    assert graduated == 0
    assert model.factors[0].status == "demoted"
    assert "survival_rate=0.333" in model.factors[0].lesson


def test_walkforward_empty_predictions_do_not_vacuously_graduate(tmp_path: Path) -> None:
    db_path = tmp_path / "ema_backtest_runs.db"
    _seed_run(db_path)

    report = evaluate_walkforward(
        family="ema",
        thesis_id="thesis-001",
        runtime_root=tmp_path,
        db_path=db_path,
        run_id="run-thesis",
        windows=_windows(),
        predictions=[],
        baseline_metrics=[
            {"profit_factor": 1.0, "trade_count": 30},
            {"profit_factor": 1.0, "trade_count": 30},
            {"profit_factor": 1.0, "trade_count": 30},
        ],
        candidate_metrics=[
            {"profit_factor": 1.2, "trade_count": 30},
            {"profit_factor": 1.1, "trade_count": 30},
            {"profit_factor": 1.3, "trade_count": 30},
        ],
    )

    assert report["graduated"] is False
    assert report["survival_rate"] == 0.0


def test_walkforward_direction_rules_match_registered_prediction_evaluator(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ema_backtest_runs.db"
    _seed_run(db_path)
    registered = tmp_path / "registered_predictions.json"
    registered.write_text(
        json.dumps(
            {
                "thesis_id": "thesis-001",
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 1.0}
                ],
            }
        ),
        encoding="utf-8",
    )

    harvest = evaluate_predictions(
        registered,
        baseline={"profit_factor": 1.0, "trade_count": 30},
        candidate={"profit_factor": 1.0, "trade_count": 30},
    )
    report = evaluate_walkforward(
        family="ema",
        thesis_id="thesis-001",
        runtime_root=tmp_path,
        db_path=db_path,
        run_id="run-thesis",
        windows=build_windows("2020-01-01", "2020-10-01"),
        predictions=[{"metric": "profit_factor", "direction": "increase", "predicted": 1.0}],
        baseline_metrics=[{"profit_factor": 1.0, "trade_count": 30}],
        candidate_metrics=[{"profit_factor": 1.0, "trade_count": 30}],
    )

    assert harvest.prediction_results[0]["direction_passed"] is True
    assert report["windows"][0]["prediction_results"][0]["direction_passed"] is True


def test_run_walkforward_queue_runs_windows_and_marks_graduated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    db_path = tmp_path / "ema_backtest_runs.db"
    db = BacktestRunDB(db_path)
    baseline_config = tmp_path / "runtime/jobs/job-1/research/round-0-baseline/selected_config.json"
    candidate_config = tmp_path / "runtime/jobs/job-1/research/round-1/selected_config.json"
    baseline_config.parent.mkdir(parents=True)
    candidate_config.parent.mkdir(parents=True)
    config_payload = {
        "data_universe": "tiny",
        "validation_start": "2020-01-01",
        "validation_end": "2021-04-01",
    }
    baseline_config.write_text(json.dumps(config_payload), encoding="utf-8")
    candidate_config.write_text(
        json.dumps({**config_payload, "ema_length": 8}),
        encoding="utf-8",
    )
    (candidate_config.parent / "registered_predictions.json").write_text(
        json.dumps(
            {
                "thesis_id": "thesis-001",
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 1.2},
                    {"metric": "median_expectancy", "direction": "increase", "predicted": 0.2},
                ],
            }
        ),
        encoding="utf-8",
    )
    db.add_from_sqlite_fields(
        run_id="run-baseline",
        thesis_id="baseline",
        config_path="runtime/jobs/job-1/research/round-0-baseline/selected_config.json",
        runtime_config=config_payload,
        code_commit="abcdef1",
        data_hash="data",
        metrics={"profit_factor": 1.0, "trade_count": 30},
        trade_analysis={},
        strategy_diagnostics={},
        decision_status="keep",
        verdict_status="supported",
        verdict_summary="supported",
        family="ema",
        job_id=1,
        primary_metric_name="profit_factor",
        primary_metric_value=1.0,
        research_round_id="job-1-round-0",
        research_round_number=0,
        is_baseline=True,
    )
    db.add_from_sqlite_fields(
        run_id="run-thesis",
        thesis_id="thesis-001",
        config_path="runtime/jobs/job-1/research/round-1/selected_config.json",
        runtime_config={**config_payload, "ema_length": 8},
        code_commit="abcdef1",
        data_hash="data",
        metrics={"profit_factor": 1.2, "trade_count": 30},
        trade_analysis={},
        strategy_diagnostics={},
        decision_status="keep",
        verdict_status="supported",
        verdict_summary="supported",
        family="ema",
        job_id=1,
        primary_metric_name="profit_factor",
        primary_metric_value=1.2,
        research_round_id="job-1-round-1",
        research_round_number=1,
    )
    save_model(
        CausalModel(
            family="ema",
            version=1,
            factors=[
                CausalFactor(
                    factor_id="f001",
                    story="durable PF lift",
                    rule="gap_pct < 0",
                    direction="win",
                    evidence_rounds=[1],
                    status="harvested",
                )
            ],
            accuracy_history=[],
        )
    )
    written_states: list[dict] = []
    commands: list[str] = []

    class Family:
        name = "ema"

        def benchmark_command(self, config_path: str, output_dir: str | None = None) -> str:
            return f"{config_path}|{output_dir}"

    class Controller:
        root = tmp_path
        runtime_root = tmp_path
        family = Family()
        backtest_run_db = db

        def run_command(self, command: str) -> tuple[int, str]:
            commands.append(command)
            is_candidate = "/candidate" in command
            metric = 1.2 if is_candidate else 1.0
            expectancy = 0.42 if is_candidate else 0.10
            return 0, json.dumps(
                {
                    "profit_factor": metric,
                    "metrics": {"trade_count": 30},
                    "train_metrics": {"median_expectancy": expectancy},
                }
            )

        def parse_metric(self, output: str, name: str = "profit_factor") -> float:
            return float(json.loads(output)[name])

        def parse_benchmark_details(self, output: str) -> dict:
            return json.loads(output)

        def primary_metric_name(self) -> str:
            return "profit_factor"

        def write_state(self, state: dict) -> None:
            written_states.append(dict(state))

        def write_current_md(self, state: dict, results: list) -> None:
            pass

        def read_results(self) -> list:
            return []

    exit_code = run_walkforward_queue(
        Controller(),
        {
            "state": "running",
            "job": 1,
            "research_round": 6,
            "next_action": {"type": "walkforward"},
            "finished_reason": "model_plateau_pending_walkforward",
        },
    )

    with sqlite3.connect(db_path) as conn:
        graduated = conn.execute(
            "SELECT graduated FROM backtest_runs WHERE run_id = 'run-thesis'"
        ).fetchone()[0]
    report = json.loads((tmp_path / "walkforward" / "thesis-001.json").read_text())
    assert exit_code == 0
    assert len(commands) == 6
    assert graduated == 1
    assert report["graduated"] is True
    assert report["windows"][0]["prediction_results"][1]["metric"] == "median_expectancy"
    assert report["windows"][0]["prediction_results"][1]["direction_passed"] is True
    assert written_states[-1]["walkforward_status"] == "completed"


def test_run_walkforward_queue_skips_candidates_without_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    baseline_config = tmp_path / "runtime/jobs/job-1/research/round-0-baseline/selected_config.json"
    candidate_config = tmp_path / "runtime/jobs/job-1/research/round-1/selected_config.json"
    baseline_config.parent.mkdir(parents=True)
    candidate_config.parent.mkdir(parents=True)
    config_payload = {
        "data_universe": "tiny",
        "validation_start": "2020-01-01",
        "validation_end": "2020-02-01",
    }
    baseline_config.write_text(json.dumps(config_payload), encoding="utf-8")
    candidate_config.write_text(json.dumps({**config_payload, "ema_length": 8}), encoding="utf-8")
    (candidate_config.parent / "registered_predictions.json").write_text(
        json.dumps(
            {
                "thesis_id": "thesis-001",
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 1.2}
                ],
            }
        ),
        encoding="utf-8",
    )
    db.add_from_sqlite_fields(
        run_id="run-baseline",
        thesis_id="baseline",
        config_path="runtime/jobs/job-1/research/round-0-baseline/selected_config.json",
        runtime_config=config_payload,
        code_commit="abcdef1",
        data_hash="data",
        metrics={"profit_factor": 1.0, "trade_count": 30},
        trade_analysis={},
        strategy_diagnostics={},
        decision_status="keep",
        verdict_status="supported",
        verdict_summary="supported",
        family="ema",
        job_id=1,
        primary_metric_name="profit_factor",
        primary_metric_value=1.0,
        research_round_id="job-1-round-0",
        research_round_number=0,
        is_baseline=True,
    )
    db.add_from_sqlite_fields(
        run_id="run-thesis",
        thesis_id="thesis-001",
        config_path="runtime/jobs/job-1/research/round-1/selected_config.json",
        runtime_config={**config_payload, "ema_length": 8},
        code_commit="abcdef1",
        data_hash="data",
        metrics={"profit_factor": 1.2, "trade_count": 30},
        trade_analysis={},
        strategy_diagnostics={},
        decision_status="keep",
        verdict_status="supported",
        verdict_summary="supported",
        family="ema",
        job_id=1,
        primary_metric_name="profit_factor",
        primary_metric_value=1.2,
        research_round_id="job-1-round-1",
        research_round_number=1,
    )
    commands: list[str] = []
    written_states: list[dict] = []

    class Family:
        name = "ema"

        def benchmark_command(self, config_path: str, output_dir: str | None = None) -> str:
            return f"{config_path}|{output_dir}"

    class Controller:
        root = tmp_path
        runtime_root = tmp_path
        family = Family()
        backtest_run_db = db

        def run_command(self, command: str) -> tuple[int, str]:
            commands.append(command)
            return 0, json.dumps({"profit_factor": 1.0, "trade_count": 30})

        def parse_metric(self, output: str, name: str = "profit_factor") -> float:
            return float(json.loads(output)[name])

        def parse_benchmark_details(self, output: str) -> dict:
            return json.loads(output)

        def primary_metric_name(self) -> str:
            return "profit_factor"

        def write_state(self, state: dict) -> None:
            written_states.append(dict(state))

        def write_current_md(self, state: dict, results: list) -> None:
            pass

        def read_results(self) -> list:
            return []

    exit_code = run_walkforward_queue(
        Controller(),
        {
            "state": "running",
            "job": 1,
            "research_round": 6,
            "next_action": {"type": "walkforward"},
            "finished_reason": "model_plateau_pending_walkforward",
        },
    )

    assert exit_code == 0
    assert commands == []
    assert not (tmp_path / "walkforward" / "thesis-001.json").exists()
    assert written_states[-1]["walkforward_status"] == "completed"


def test_walkforward_config_reads_nested_defaults() -> None:
    config = {"research_engine": {"walkforward": {"survival_pct": 0.75}}}

    assert walkforward_config(config) == {
        "train_months": 6,
        "test_months": 3,
        "step_months": 3,
        "survival_pct": 0.75,
    }
