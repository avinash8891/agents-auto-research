from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autoresearch_controller import AutoresearchController
from experiment_db import ExperimentDB
from strategy_family import load_family


def test_experiment_db_creates_real_sqlite_database_file(tmp_path) -> None:
    db_path = tmp_path / "ema_experiments.db"
    db = ExperimentDB(db_path)
    db.init_session(name="ema", metric_name="median_expectancy", direction="higher")

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert "session_meta" in tables
    assert "experiments" in tables
    assert "research_rounds" in tables
    assert "research_thesis_attempts" in tables


def test_experiment_db_persists_experiment_rows_to_sqlite(tmp_path) -> None:
    db_path = tmp_path / "ema_experiments.db"
    db = ExperimentDB(db_path)
    db.init_session(name="ema", metric_name="median_expectancy", direction="higher")
    db.add_from_sqlite_fields(
        experiment_id="e1",
        thesis_id="t1",
        config_path="configs/ema_base.yaml",
        runtime_config={"ema_length": 5},
        code_commit="abc123",
        data_hash="data1",
        metrics={"median_expectancy": 1.25, "trade_count": 10},
        trade_analysis={"trade_count": 10},
        strategy_diagnostics={"rejection_breakdown": {}},
        decision_status="keep",
        verdict_status="none",
        verdict_summary="",
        family="ema",
        job_id=1,
        run_id="run-1",
        primary_metric_name="median_expectancy",
        primary_metric_value=1.25,
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT experiment_id, thesis_id, config_path, accepted FROM experiments"
        ).fetchone()

    assert row == ("e1", "t1", "configs/ema_base.yaml", 1)


def test_sqlite_session_meta_required_fields_are_non_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "ema_experiments.db"
    db = ExperimentDB(db_path)
    db.init_session(name="ema", metric_name="median_expectancy", direction="higher")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT name, metric_name, best_direction FROM session_meta WHERE id = 1"
        ).fetchone()

    assert row == ("ema", "median_expectancy", "higher")


def test_sqlite_research_round_required_fields_are_non_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "ema_experiments.db"
    db = ExperimentDB(db_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"state": "running", "job": 2}))

    db.log_research_round(
        state_path,
        round_number=4,
        thesis_id="ema5",
        hypothesis_id="ema5",
        outcome="compiled",
        usage={"total": {"total_tokens": 12}},
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("""
            SELECT research_round_id, job_id, round_number, run_id, hypothesis_id,
                   selected_thesis_id, outcome, created_at_utc, usage_json
            FROM research_rounds
            """).fetchone()

    assert row[0] == "job-2-round-4"
    assert row[1] == 2
    assert row[2] == 4
    assert row[3]
    assert row[4] == "ema5"
    assert row[5] == "ema5"
    assert row[6] == "compiled"
    assert row[7]
    assert json.loads(row[8]) == {"total": {"total_tokens": 12}}


def test_sqlite_research_thesis_attempt_required_fields_are_non_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "ema_experiments.db"
    db = ExperimentDB(db_path)
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-2-round-4",
            "attempt_number": 1,
            "thesis_id": "ema5",
            "strategy_family": "ema",
            "config_changes": {"ema_length": 5},
            "validator_status": "compiled",
            "mechanism_dimension": "timing",
            "hypothesis": "ema 5 should react faster",
            "mechanism": "reduce lag in entries",
            "rejection_reason": "",
            "selected_for_execution": 1,
            "created_at_utc": "2026-04-30T00:00:00+00:00",
        }
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("""
            SELECT research_round_id, attempt_number, thesis_id, strategy_family,
                   config_changes_json, validator_status, mechanism_dimension,
                   hypothesis, mechanism, rejection_reason, selected_for_execution,
                   created_at_utc
            FROM research_thesis_attempts
            """).fetchone()

    assert row[0] == "job-2-round-4"
    assert row[1] == 1
    assert row[2] == "ema5"
    assert row[3] == "ema"
    assert json.loads(row[4]) == {"ema_length": 5}
    assert row[5] == "compiled"
    assert row[6] == "timing"
    assert row[7] == "ema 5 should react faster"
    assert row[8] == "reduce lag in entries"
    assert row[9] == ""
    assert row[10] == 1
    assert row[11] == "2026-04-30T00:00:00+00:00"


def test_sqlite_experiment_required_fields_are_meaningfully_populated_in_real_ema5_flow(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    family = load_family("ema")
    runtime_root = (tmp_path / "runtime").resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    controller = AutoresearchController(
        root=runtime_root,
        state_path=runtime_root / "ema_autoresearch.next.json",
        current_md_path=runtime_root / "ema_autoresearch.current.md",
        ideas_md_path=runtime_root / "ema_autoresearch.ideas.md",
        runs_dir=runtime_root / family.runs_dirname,
        family=family,
    )
    controller.experiment_db = ExperimentDB(runtime_root / "ema_experiments.db")
    controller.write_entries(
        [
            {
                "type": "config",
                "name": "ema",
                "metricName": "median_expectancy",
                "metricUnit": "",
                "bestDirection": "higher",
            }
        ]
    )
    fixture = json.loads((repo_root / "tests" / "fixtures" / "tiny_ema_runtime.json").read_text())
    fixture["ema_length"] = 5
    config_rel = "experiments/ema5/runtime_config.json"
    config_path = runtime_root / config_rel
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(fixture))
    controller.ctx.current_contract = SimpleNamespace(
        experiment_id="ema5",
        thesis_id="ema5",
        hypothesis="ema 5 should react faster",
        mechanism="reduce lag in entries",
    )
    controller.ctx.parent_experiment_id = "parent-123"
    controller.write_state(
        {
            "state": "running",
            "job": 1,
            "research_round": 1,
            "blockers": [],
            "_last_round_usage": {"prompt_tokens": 10},
        }
    )
    run_output_dir = runtime_root / family.runs_dirname / "job-1" / "c2821b0a43ba"
    run_output_dir.mkdir(parents=True, exist_ok=True)
    command = family.benchmark_command(str(config_path), run_output_dir)
    original_pythonpath = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = (
        f"{repo_root}{os.pathsep}{original_pythonpath}" if original_pythonpath else str(repo_root)
    )
    try:
        code, output = controller.run_command(command)
    finally:
        if original_pythonpath:
            os.environ["PYTHONPATH"] = original_pythonpath
        else:
            os.environ.pop("PYTHONPATH", None)
    assert code == 0
    metric = controller.parse_metric(output, controller.primary_metric_name())
    decision = controller.evaluate_metric(metric)
    analysis = controller.derive_trade_analysis(config_rel, metric, decision, output=output)
    controller.log_experiment_result(
        config=config_rel,
        metric=metric,
        decision=decision,
        output=output,
        analysis=analysis,
    )

    with sqlite3.connect(runtime_root / "ema_experiments.db") as conn:
        row = conn.execute("""
            SELECT experiment_id, thesis_id, config_path, runtime_config_json, code_commit,
                   data_hash, train_metrics_json, validation_metrics_json, trade_count,
                   trades_file, strategy_events_file, diagnostics_file,
                   strategy_diagnostics_json, accepted, rejection_reason, verdict_status,
                   verdict_summary, parent_experiment_id, timestamp, family, hypothesis,
                   mechanism, job, usage_json, asi_json, description
            FROM experiments
            ORDER BY rowid DESC
            LIMIT 1
            """).fetchone()

    assert row[0] == "ema5"
    assert row[1] == "ema5"
    assert row[2] == "experiments/ema5/runtime_config.json"
    assert json.loads(row[3])["ema_length"] == 5
    assert row[4]
    assert row[5]
    assert json.loads(row[6])["median_expectancy"] is not None
    assert json.loads(row[7])["trade_count"] >= 0
    assert row[8] >= 0
    assert row[9].endswith("trades.csv")
    assert row[10].endswith("strategy_events.parquet")
    assert row[11].endswith("diagnostics.json")
    assert isinstance(json.loads(row[12]), dict)
    assert row[13] in (0, 1)
    assert row[14] == "N/A"
    assert row[15] == "accepted"
    assert row[16]
    assert row[17] == "parent-123"
    assert row[18]
    assert row[19] == "ema"
    assert row[20] == "ema 5 should react faster"
    assert row[21] == "reduce lag in entries"
    assert row[22] == 1
    assert json.loads(row[23]) == {"prompt_tokens": 10}
    asi = json.loads(row[24])
    assert asi["run_id"] == "job-1-run-1-ema5"
    assert asi["hypothesis_id"] == "ema5"
    assert asi["artifact_dir"] == "ema_autoresearch-runs/job-1/ema5"
    assert row[25] == "strict-native loop: ema5"


def test_sqlite_research_fields_are_meaningfully_populated_from_real_ready_thesis() -> None:
    from autoresearch_research import run_research

    class _Controller:
        def __init__(self) -> None:
            self.root = Path(__file__).resolve().parents[1]
            self.family = SimpleNamespace(
                name="ema",
                benchmark_command=lambda config: f"python3 -m backtest.runner --strategy ema --config {config}",
            )
            self.research_dir = self.root / "ema-research"
            self.state = {"state": "blocked", "job": 7, "research_round": 0, "job_usage": None}
            self.round_kwargs = None

        def read_state(self) -> dict[str, object]:
            return dict(self.state)

        def write_state(self, state: dict[str, object]) -> None:
            self.state = dict(state)

        def _accumulate_job_usage(self, round_usage: dict[str, object]) -> None:
            self.state["job_usage"] = round_usage

        def execute_research_one(self) -> dict[str, object]:
            return {
                "status": "completed",
                "generated_config": "experiments/ema5/runtime_config.json",
                "generated_config_needs_build": False,
                "generated_thesis_id": "ema5",
                "thesis_id": "ema5",
                "experiment_id": "ema5",
                "should_stop": False,
                "reasoning": "research rationale",
                "thesis": {
                    "thesis_id": "ema5",
                    "strategy_family": "ema",
                    "config_changes": {"ema_length": 5},
                    "hypothesis": "ema 5 should react faster",
                    "mechanism": "reduce lag in entries",
                    "mechanism_dimension": "timing",
                },
            }

        def log_research_round(self, **kwargs: object) -> None:
            self.round_kwargs = kwargs

    controller = _Controller()
    state = controller.read_state()

    with (
        patch("trace_sdk.begin_round", lambda *_: None),
        patch("research_conductor.reset_round_usage", lambda: None),
        patch(
            "research_conductor.get_round_usage",
            lambda: {"total": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}},
        ),
    ):
        out = run_research(controller, state)

    assert out["next_action"]["config"] == "experiments/ema5/runtime_config.json"
    assert controller.round_kwargs is not None
    assert controller.round_kwargs["thesis_id"] == "ema5"
    assert controller.round_kwargs["config_changes"] == {"ema_length": 5}
    assert controller.round_kwargs["hypothesis"] == "ema 5 should react faster"
    assert controller.round_kwargs["mechanism"] == "reduce lag in entries"
    assert controller.round_kwargs["mechanism_dimension"] == "timing"


def test_handle_success_preserves_contract_for_follow_on_experiment() -> None:
    from autoresearch_research import _handle_success

    controller = SimpleNamespace(
        family=SimpleNamespace(
            benchmark_command=lambda config: f"python3 -m backtest.runner --strategy ema --config {config}"
        ),
        ctx=SimpleNamespace(current_contract=SimpleNamespace(thesis_id="ema5", mechanism="m")),
        write_state=lambda state: None,
    )
    state = {"state": "blocked", "blockers": [{"kind": "research_required"}]}
    result = {"generated_config": "experiments/ema5/runtime_config.json", "thesis_id": "ema5"}

    out = _handle_success(controller, state, result, research_round=1)

    assert out["next_action"]["config"] == "experiments/ema5/runtime_config.json"
    assert controller.ctx.current_contract.thesis_id == "ema5"


def test_experiment_record_prefers_contract_metadata_over_runtime_config_stem(
    tmp_path: Path,
) -> None:
    from autoresearch_experiment import _build_db_record

    controller = SimpleNamespace(
        family=SimpleNamespace(name="ema"),
        current_commit=lambda: "abc1234",
        ctx=SimpleNamespace(
            current_contract=SimpleNamespace(
                experiment_id="exp-1",
                thesis_id="time_stop_reversion_decay",
                hypothesis="hypothesis text",
                mechanism="mechanism text",
            ),
            latest_config_contents={"ema_length": 5},
            parent_experiment_id="parent-1",
        ),
    )
    record = _build_db_record(
        controller,
        config="experiments/5fad7f19bbba/runtime_config.json",
        decision="keep",
        details={"trade_count": 1, "train_metrics": {"median_expectancy": 1.0}},
        analysis={"trade_analysis": {}},
        fallback_experiment_id="fallback",
        state={"job": 3, "_last_round_usage": {}},
    )

    assert record.thesis_id == "time_stop_reversion_decay"
    assert record.hypothesis == "hypothesis text"
    assert record.mechanism == "mechanism text"
