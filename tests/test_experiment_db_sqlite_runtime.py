from __future__ import annotations

import sqlite3
from pathlib import Path

from backtest_run_db import BacktestRunDB, BacktestRunRecord


def _record(*, round_number: int, job: int, baseline: bool = False) -> BacktestRunRecord:
    backtest_dir = (
        f"runtime/jobs/job-{job}/research/round-0-baseline/backtest"
        if baseline
        else f"runtime/jobs/job-{job}/research/round-{round_number}/backtest"
    )
    return BacktestRunRecord(
        run_id=f"exp-{job}-{round_number}",
        thesis_id="baseline" if baseline else f"thesis-{round_number}",
        config_path=(
            "configs/ema_base.yaml"
            if baseline
            else f"runtime/jobs/job-{job}/research/round-{round_number}/selected_config.json"
        ),
        runtime_config={},
        code_commit="abc1234",
        data_hash="data",
        train_metrics={},
        validation_metrics={"profit_factor": 1.5},
        trade_count=10,
        trades_file=f"{backtest_dir}/trades.csv",
        strategy_events_file=f"{backtest_dir}/strategy_events.parquet",
        diagnostics_file=f"{backtest_dir}/diagnostics.json",
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="good",
        parent_backtest_run_id="",
        timestamp="2026-05-09T00:00:00+00:00",
        family="ema",
        hypothesis="h",
        mechanism="m",
        job=job,
        backtest_run_id=f"job-{job}-round-{round_number}-backtest",
        research_round_id=f"job-{job}-round-{round_number}",
        research_round_number=round_number,
        is_baseline=baseline,
    )


def test_sqlite_schema_keeps_backtest_runs_table_with_round_columns(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    with sqlite3.connect(db.path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)")}

    assert "research_round_id" in columns
    assert "research_round_number" in columns
    assert "backtest_run_id" in columns


def test_read_results_reports_round_scoped_artifact_dir(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    db.add(_record(round_number=1, job=1))

    result = db.read_results()[0]

    assert result.asi["artifact_dir"] == "runtime/jobs/job-1/research/round-1/backtest"
    assert result.asi["research_round_id"] == "job-1-round-1"
    assert result.asi["backtest_run_id"] == "job-1-round-1-backtest"


def test_export_entries_use_backtest_run_type(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    db.add(_record(round_number=0, job=3, baseline=True))

    entries = [entry for entry in db.export_entries() if entry.get("type") != "config"]

    assert entries[0]["type"] == "backtest_run"
    assert entries[0]["is_baseline"] is True
    assert entries[0]["research_round_number"] == 0


def test_list_research_rounds_keeps_round_zero_baseline(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    state_path = tmp_path / "state.json"
    state_path.write_text('{"job": 3}\n')
    db.log_research_round(
        state_path,
        round_number=0,
        thesis_id="baseline",
        hypothesis_id="baseline",
        outcome="baseline_backtest_completed",
    )

    rows = db.list_research_rounds()

    assert rows[0]["round_number"] == 0
    assert rows[0]["selected_thesis_id"] == "baseline"


def test_research_thesis_attempt_schema_has_attempt_id_and_required_indexes(
    tmp_path: Path,
) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")

    with sqlite3.connect(db.path) as conn:
        attempt_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(research_thesis_attempts)")
        }
        attempt_index_names = {
            row[1] for row in conn.execute("PRAGMA index_list(research_thesis_attempts)")
        }
        round_index_names = {row[1] for row in conn.execute("PRAGMA index_list(research_rounds)")}

    assert "thesis_attempt_id" in attempt_columns
    assert "idx_research_rounds_job_round" in round_index_names
    assert "idx_research_rounds_outcome" in round_index_names
    assert "idx_research_thesis_attempts_round_attempt" in attempt_index_names
    assert "idx_research_thesis_attempts_validator_status" in attempt_index_names


def test_research_round_retry_attempts_remain_first_class_rows(
    tmp_path: Path,
) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            """
            INSERT INTO research_rounds (
                research_round_id, job_id, round_number, run_id, hypothesis_id,
                selected_thesis_id, outcome, created_at_utc, usage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-17-round-2",
                17,
                2,
                "run-17",
                "ema-tight-entry",
                "ema-tight-entry",
                "compiled",
                "2026-05-28T00:00:00+00:00",
                "{}",
            ),
        )
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-17-round-2",
            "attempt_number": 1,
            "thesis_id": "ema-duplicate",
            "strategy_family": "ema",
            "config_changes": {"ema_length": 8},
            "validator_status": "rejected_attempt_1",
            "validation_failure_reason": "duplicate mechanism",
            "selected_for_execution": 0,
            "created_at_utc": "2026-05-28T00:00:01+00:00",
        }
    )
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-17-round-2",
            "attempt_number": 2,
            "thesis_id": "ema-metadata-leak",
            "strategy_family": "ema",
            "config_changes": {"ema_length": 13},
            "validator_status": "rejected_attempt_2",
            "validation_failure_reason": "config_changes contains thesis metadata key",
            "selected_for_execution": 0,
            "created_at_utc": "2026-05-28T00:00:02+00:00",
        }
    )
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-17-round-2",
            "attempt_number": 3,
            "thesis_id": "ema-tight-entry",
            "strategy_family": "ema",
            "config_changes": {"ema_length": 21},
            "validator_status": "compiled",
            "hypothesis": "tighten entry after weak crosses",
            "mechanism": "avoid low-conviction crosses",
            "selected_for_execution": 1,
            "created_at_utc": "2026-05-28T00:00:03+00:00",
        }
    )

    attempts = db.list_research_thesis_attempts(job_id=17)
    by_attempt = {attempt["attempt_number"]: attempt for attempt in attempts}

    assert sorted(by_attempt) == [1, 2, 3]
    assert by_attempt[1]["thesis_attempt_id"] == "job-17-round-2-attempt-1"
    assert by_attempt[1]["validator_status"] == "rejected_attempt_1"
    assert by_attempt[1]["selected_for_execution"] == 0
    assert by_attempt[2]["thesis_attempt_id"] == "job-17-round-2-attempt-2"
    assert by_attempt[2]["validator_status"] == "rejected_attempt_2"
    assert by_attempt[2]["validation_failure_reason"] == (
        "config_changes contains thesis metadata key"
    )
    assert by_attempt[2]["selected_for_execution"] == 0
    assert by_attempt[3]["thesis_attempt_id"] == "job-17-round-2-attempt-3"
    assert by_attempt[3]["validator_status"] == "compiled"
    assert by_attempt[3]["selected_for_execution"] == 1
    assert by_attempt[3]["hypothesis"] == "tighten entry after weak crosses"
    assert by_attempt[3]["mechanism"] == "avoid low-conviction crosses"
    assert by_attempt[3]["created_at_utc"] == "2026-05-28T00:00:03+00:00"


def test_best_by_metric_ignores_malformed_metric_values(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    first = _record(round_number=1, job=1)
    first.validation_metrics["profit_factor"] = "not-a-number"
    second = _record(round_number=2, job=1)
    second.validation_metrics["profit_factor"] = 1.75

    db.add(first)
    db.add(second)

    best = db.best_by_metric("profit_factor")

    assert best is not None
    assert best.run_id == second.run_id
